"""WSGI and local development entry point."""

from studyai import create_app

app = create_app()


if __name__ == "__main__":
    app.run()
