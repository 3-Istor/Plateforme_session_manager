import os


# These values must be present before backend.app.database imports Settings.
# They keep the test suite isolated from a deployment-oriented local .env.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["AUTH_MODE"] = "demo"
os.environ["ALLOWED_HOSTS"] = "localhost,testserver"
