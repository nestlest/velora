import os
from dotenv import load_dotenv

load_dotenv()

def get_postgres_miner_url():
    POSTGRES_USER = os.getenv("POSTGRES_MINER_USER")
    POSTGRES_DB = os.getenv("POSTGRES_MINER_DB")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_MINER_PASSWORD")
    POSTGRES_HOST = os.getenv("POSTGRES_MINER_HOST")
    POSTGRES_PORT = os.getenv("POSTGRES_MINER_PORT")
    DATABASE_URL = f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    
    return DATABASE_URL

def get_postgres_validator_url():
    POSTGRES_USER = os.getenv("POSTGRES_VALIDATOR_USER")
    POSTGRES_DB = os.getenv("POSTGRES_VALIDATOR_DB")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_VALIDATOR_PASSWORD")
    POSTGRES_HOST = os.getenv("POSTGRES_VALIDATOR_HOST")
    POSTGRES_PORT = os.getenv("POSTGRES_VALIDATOR_PORT")
    DATABASE_URL = f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    
    return DATABASE_URL