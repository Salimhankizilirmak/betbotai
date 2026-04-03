import traceback
try:
    import main
except Exception as e:
    with open("crash.txt", "w") as f:
        f.write(traceback.format_exc())
