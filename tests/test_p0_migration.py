import sys
sys.path.insert(0, r"D:\git\github\fwquant\fwsort")

from fwsort.database import init_db

applied = init_db()
print("init_db applied:", applied)
