import os
import logging
import tweepy
from dotenv import load_dotenv

load_dotenv()

def get_x_client():
    consumer_key = os.getenv("X_CONSUMER_KEY")
    consumer_secret = os.getenv("X_CONSUMER_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_token_secret = os.getenv("X_ACCESS_TOKEN_SECRET")
    
    if not all([consumer_key, consumer_secret, access_token, access_token_secret]):
        logging.error("X API credentials missing in .env")
        return None

    # Tweepy Client uses X API v2 for posting tweets
    try:
        client = tweepy.Client(
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            access_token=access_token,
            access_token_secret=access_token_secret
        )
        return client
    except Exception as e:
        logging.error(f"Error initializing X client: {e}")
        return None

def post_tweet(text: str):
    """
    Posts a tweet to X using the v2 API.
    Handles text length limits (max 280 characters).
    """
    client = get_x_client()
    if not client:
        return False

    # X allows 280 characters. Let's make sure we don't exceed it.
    if len(text) > 280:
        # Ellipsize if too long
        tweet_text = text[:277] + "..."
    else:
        tweet_text = text

    try:
        logging.info("Sending tweet to X...")
        response = client.create_tweet(text=tweet_text)
        logging.info(f"Tweet posted successfully. ID: {response.data['id']}")
        return True
    except tweepy.errors.TweepyException as e:
        logging.error(f"Failed to post tweet: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error posting tweet: {e}")
        return False
