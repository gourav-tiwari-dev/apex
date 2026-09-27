"""Scratch query runner:  python q.py "SELECT ..." """

import sqlite3, sys

conn = sqlite3.connect("apex.db")
cur = conn.execute(sys.argv[1])
rows = cur.fetchall()

column_names = []
for column in cur.description:
    column_names.append(str(column[0]))

# each column is as wide as its longest value, so the table lines up
widths = []
for i in range(len(column_names)):
    width = len(column_names[i])
    for row in rows:
        value_length = len(str(row[i]))
        if value_length > width:
            width = value_length
    widths.append(width)

header_cells = []
divider_cells = []
for i in range(len(column_names)):
    header_cells.append(column_names[i].ljust(widths[i]))
    divider_cells.append("-" * widths[i])
print("  ".join(header_cells))
print("  ".join(divider_cells))

for row in rows:
    cells = []
    for i in range(len(row)):
        cells.append(str(row[i]).ljust(widths[i]))
    print("  ".join(cells))

print(f"\n({len(rows)} rows)")
