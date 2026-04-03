with open("logs/betbot.log", "r", encoding="utf-8", errors="replace") as f:
    lines = f.readlines()
with open("betbot_tail.txt", "w", encoding="utf-8") as f:
    f.writelines(lines[-100:])
