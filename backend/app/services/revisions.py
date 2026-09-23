"""Reviewable changes: the original session stays authoritative until approval."""
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from ..models import (SessionRequest, SessionRevision, Participant, RequestStatus,
                      ForcedSession, ForcedBusyParticipant, Notification, DiscordDelivery)
from .user_calendar import update_manager_event


def snapshot(item):
    return {"title": item.title, "session_type": item.session_type, "agenda": item.agenda,
            "start_at": item.start_at.isoformat(), "end_at": item.end_at.isoformat(),
            "participants": sorted(p.email for p in item.participants),
            "status": item.status.value, "calendar_event_id": item.calendar_event_id}


def same_schedule(original, proposal):
    return (original.start_at == proposal.start_at and original.end_at == proposal.end_at
            and {p.email for p in original.participants} == {p.email for p in proposal.participants})


def conflicts(db, settings, original, proposal):
    from .. import main
    from .availability import BusyPeriods
    # Text-only changes do not reserve any new time/participant. In particular,
    # do not treat the already approved event itself as a new conflict.
    if original.status == RequestStatus.approved and same_schedule(original, proposal):
        return BusyPeriods()
    return main.combined_busy_periods(db, settings, [p.email for p in proposal.participants],
                                     proposal.start_at, proposal.end_at, exclude_id=original.id)


def queue_discord(db, settings, item):
    if settings.discord_webhook_url.get_secret_value() and db.get(DiscordDelivery, item.id) is None:
        db.add(DiscordDelivery(request_id=item.id))


def propose(db, settings, user, original_id, payload):
    from .. import main
    with main.scheduling_lock:
        original = db.get(SessionRequest, original_id)
        if original is None or original.revision:
            raise HTTPException(404, "Session introuvable")
        if not user.is_manager and original.requester_email != str(user.email):
            raise HTTPException(403, "Seul l'auteur ou un manager peut modifier cette session")
        if original.start_at <= datetime.now(timezone.utc) or payload.start_at <= datetime.now(timezone.utc):
            raise HTTPException(422, "Seules les sessions à venir peuvent être modifiées")
        if payload.force and not user.is_manager:
            raise HTTPException(403, "Seul le manager peut forcer un créneau")
        pending = db.scalar(select(SessionRevision.request_id).join(
            SessionRequest, SessionRequest.id == SessionRevision.request_id
        ).where(SessionRevision.original_id == original_id, SessionRequest.status == RequestStatus.pending))
        if pending:
            raise HTTPException(409, "Une modification est déjà en attente pour cette session")
        emails = main.validate_participants([str(e) for e in payload.participant_emails], settings)
        proposal = SessionRequest(requester_email=str(user.email), requester_name=user.name,
            title=payload.formatted_title, session_type=payload.session_type, agenda=payload.agenda,
            start_at=payload.start_at, end_at=payload.end_at,
            participants=[Participant(email=e) for e in emails],
            revision=SessionRevision(original_id=original.id, snapshot=json.dumps(snapshot(original))))
        busy = conflicts(db, settings, original, proposal)
        if not payload.force and main.has_conflict(busy, proposal.start_at, proposal.end_at):
            raise HTTPException(409, "Ce créneau est occupé. Choisissez un autre horaire. Un déplacement chevauchant la session actuelle peut nécessiter un forçage explicite du manager.")
        if payload.force:
            proposal.force_record = ForcedSession(
                collective_calendar_busy=main.collective_calendar_has_conflict(busy, proposal.start_at, proposal.end_at),
                busy_participants=[ForcedBusyParticipant(email=e) for e in main.busy_participant_emails(busy, proposal.start_at, proposal.end_at)])
        db.add(proposal)
        db.flush()
        queue_discord(db, settings, proposal)
        db.add(Notification(recipient_email=str(settings.manager_email), title="Modification de session à valider",
                            message=f"{user.name} propose une modification de « {original.title} » (session #{original.id}).",
                            request_id=proposal.id))
        db.commit()
        db.refresh(proposal)
        return proposal


def decide(db, settings, request_id, payload):
    from .. import main
    with main.scheduling_lock:
        proposal = db.get(SessionRequest, request_id)
        db.refresh(proposal)
        if proposal.status != RequestStatus.pending:
            raise HTTPException(409, "Cette modification a déjà été traitée")
        original = db.get(SessionRequest, proposal.revision.original_id)
        if not original:
            raise HTTPException(404, "Session d'origine introuvable")
        db.refresh(original)
        if payload.status == RequestStatus.approved:
            if snapshot(original) != json.loads(proposal.revision.snapshot):
                raise HTTPException(409, "La session a changé depuis cette demande. Refusez cette modification et créez-en une nouvelle.")
            if proposal.start_at <= datetime.now(timezone.utc):
                raise HTTPException(409, "Le créneau proposé est passé")
            busy = conflicts(db, settings, original, proposal)
            if proposal.is_forced:
                if main.refresh_forced_conflicts(proposal,
                    main.busy_participant_emails(busy, proposal.start_at, proposal.end_at),
                    main.collective_calendar_has_conflict(busy, proposal.start_at, proposal.end_at)):
                    db.commit()
                    raise HTTPException(409, "Les conflits ont changé. Relisez-les avant de confirmer à nouveau.")
            elif main.has_conflict(busy, proposal.start_at, proposal.end_at):
                raise HTTPException(409, "Un agenda est désormais occupé sur ce créneau")
            event_id = original.calendar_event_id
            if settings.auth_mode == "google":
                manager = str(settings.manager_email).lower()
                if not main.has_required_connection(db, settings, manager, True):
                    raise HTTPException(503, "Le manager doit connecter son Google Calendar")
                arguments = dict(manager_email=manager, title=proposal.title,
                    description=f"{proposal.session_type}\n\nOrdre du jour :\n{proposal.agenda}",
                    start_at=proposal.start_at, end_at=proposal.end_at,
                    attendees=[p.email for p in proposal.participants])
                try:
                    event_id = (update_manager_event(db, settings, event_id=event_id, **arguments) if event_id
                                else main.create_manager_event(db, settings, request_key=f"session-request-{original.id}", **arguments))
                except Exception as exc:
                    raise HTTPException(502, "La mise à jour Google Calendar a échoué ; la modification reste en attente") from exc
            for field in ("title", "session_type", "agenda", "start_at", "end_at"):
                setattr(original, field, getattr(proposal, field))
            original.participants = [Participant(email=p.email) for p in proposal.participants]
            original.status = RequestStatus.approved
            original.manager_note = payload.manager_note
            original.calendar_event_id = event_id
            proposal.calendar_event_id = event_id
            # Keep the current session's forced-conflict marker in sync.
            if not same_schedule_from_snapshot(proposal) or proposal.is_forced:
                if proposal.is_forced:
                    if original.force_record is None:
                        original.force_record = ForcedSession(collective_calendar_busy=False)
                    main.refresh_forced_conflicts(original, proposal.busy_participant_emails, proposal.collective_calendar_busy)
                else:
                    original.force_record = None
            queue_discord(db, settings, original)
        proposal.status = payload.status
        proposal.manager_note = payload.manager_note
        queue_discord(db, settings, proposal)
        recipients = {proposal.requester_email, original.requester_email,
                      *[p.email for p in original.participants],
                      *[p.email for p in proposal.participants],
                      *json.loads(proposal.revision.snapshot)["participants"]}
        for email in recipients:
            db.add(Notification(recipient_email=email, title="Modification acceptée" if payload.status == RequestStatus.approved else "Modification refusée",
                message=f"Modification de la session #{original.id} : {proposal.title}.", request_id=proposal.id))
        db.commit()
        db.refresh(proposal)
        return proposal


def same_schedule_from_snapshot(proposal):
    previous = json.loads(proposal.revision.snapshot)
    return (datetime.fromisoformat(previous["start_at"]) == proposal.start_at
            and datetime.fromisoformat(previous["end_at"]) == proposal.end_at
            and set(previous["participants"]) == {p.email for p in proposal.participants})
