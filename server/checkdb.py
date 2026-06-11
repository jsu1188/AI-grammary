import sqlite3

conn = sqlite3.connect("app.db")
cur = conn.cursor()

cur.execute("SELECT id, username, password_hash FROM users;")
rows = cur.fetchall()

print(rows)

conn.close()