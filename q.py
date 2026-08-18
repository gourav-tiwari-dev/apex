"""Scratch query runner:  python q.py "SELECT ..." """
import sqlite3, sys

conn = sqlite3.connect("apex.db")
cur = conn.execute(sys.argv[1])
cols = [d[0] for d in cur.description]
rows = cur.fetchall()

w = [max(len(str(c)), *(len(str(r[i])) for r in rows)) if rows else len(str(c))
     for i, c in enumerate(cols)]
print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(cols)))
print("  ".join("-" * w[i] for i in range(len(cols))))
for r in rows:
    print("  ".join(str(v).ljust(w[i]) for i, v in enumerate(r)))
print(f"\n({len(rows)} rows)")
