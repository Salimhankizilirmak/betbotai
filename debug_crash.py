import traceback

try:
    import main
except Exception as e:
    with open("error_details.txt", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
