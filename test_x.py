import os
import tweepy
from dotenv import load_dotenv

load_dotenv()

consumer_key = os.getenv("X_CONSUMER_KEY")
consumer_secret = os.getenv("X_CONSUMER_SECRET")
access_token = os.getenv("X_ACCESS_TOKEN")
access_token_secret = os.getenv("X_ACCESS_TOKEN_SECRET")

try:
    client = tweepy.Client(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=access_token,
        access_token_secret=access_token_secret
    )
    print("Attempting to post test tweet...")
    res = client.create_tweet(text="Test tweet from AI agent to verify WRITE permissions! 🤖")
    print("Tweet posted! ID:", res.data['id'])
except Exception as e:
    print(f"Error posting tweet: {e}")
