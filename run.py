from app import create_app
from app.db_init import init_db

app = create_app()

if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("Starting server...\n")
    app.run(debug=True)
