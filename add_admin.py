import sys
from utils.db import get_db_connection
from utils.auth_helpers import hash_password

def create_admin():
    print("\n--- EduManage: Add New Admin Account ---")
    
    if len(sys.argv) == 4:
        full_name = sys.argv[1].strip()
        email = sys.argv[2].strip().lower()
        password = sys.argv[3].strip()
    else:
        full_name = input("Enter Full Name: ").strip()
        email = input("Enter Email Address: ").strip().lower()
        password = input("Enter Password: ").strip()

    if not full_name or not email or not password:
        print("Error: All fields (name, email, password) are required.")
        return

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if email already exists
        cursor.execute("SELECT admin_id FROM admins WHERE LOWER(email) = %s", (email,))
        if cursor.fetchone():
            print(f"Error: An admin account with email '{email}' already exists.")
            cursor.close()
            conn.close()
            return

        # Securely hash password
        hashed_pwd = hash_password(password)

        cursor.execute("""
            INSERT INTO admins (full_name, email, password)
            VALUES (%s, %s, %s)
        """, (full_name, email, hashed_pwd))

        cursor.close()
        conn.close()

        print(f"\nSuccess! Admin account created for '{full_name}' ({email}).")
        print("You can now log in at /login using these credentials.")
    except Exception as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    create_admin()
