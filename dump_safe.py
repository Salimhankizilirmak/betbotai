import traceback
import sys

try:
    import main
except Exception as e:
    tb = traceback.format_exc().split('\n')
    print("START")
    for line in tb[:15]: print(line)
    print("END TRACEBACK TAIL")
    for line in tb[-15:]: print(line)
