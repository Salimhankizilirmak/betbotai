with open("tweet_error.log", "r", encoding="utf-16le") as f:
    text = f.read()
    
# Clean it up and print carefully
pieces = text.split("Error posting tweet:")
if len(pieces) > 1:
    print(pieces[1].strip()[:500])
else:
    print(repr(text))
