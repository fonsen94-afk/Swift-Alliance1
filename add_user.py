"""
Add a user to users.json for the Swift Alliance Streamlit app.

Usage:
  python add_user.py --username <username> --password <password>

Example:
  python add_user.py --username user --password pass

Security:
 - Passwords are hashed (SHA256 + salt) before being written to users.json.
 - Do NOT commit users.json with real production credentials to public repos.
"""
import os
import json
import argparse
import hashlib
import sys

ROOT_DIR = os.path.dirname(__file__)
USERS_FILE = os.path.join(ROOT_DIR, "users.json")
SALT = "swift_alliance_app_salt_2025"

def hash_password(password: str) -> str:
    return hashlib.sha256((password + SALT).encode()).hexdigest()

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"users": []}
    return {"users": []}

def save_users(data):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def user_exists(data, username):
    for u in data.get("users", []):
        if u.get("username") == username:
            return True
    return False

def add_user(username, password):
    data = load_users()
    if user_exists(data, username):
        print(f"User '{username}' already exists. Use --force to overwrite.")
        return 1
    hashed = hash_password(password)
    data.setdefault("users", []).append({"username": username, "password": hashed})
    save_users(data)
    print(f"Added user '{username}' to {USERS_FILE}")
    return 0

def overwrite_user(username, password):
    data = load_users()
    new_users = [u for u in data.get("users", []) if u.get("username") != username]
    new_users.append({"username": username, "password": hash_password(password)})
    data["users"] = new_users
    save_users(data)
    print(f"Overwritten user '{username}' in {USERS_FILE}")
    return 0

def main():
    parser = argparse.ArgumentParser(description="Add or overwrite a user for the Swift Alliance app")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--force", action="store_true", help="Overwrite existing user if present")
    args = parser.parse_args()

    if args.force:
        sys.exit(overwrite_user(args.username, args.password))
    else:
        sys.exit(add_user(args.username, args.password))

if __name__ == "__main__":
    main()