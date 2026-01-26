# from pymongo import MongoClient

# def get_db():
#     client = MongoClient("mongodb://localhost:27017/")
#     return client["mydatabase"]


# server/db_config.py
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os


def get_connection():
    connection = psycopg2.connect(
        dbname= os.getenv("dbname"),          # your database name
        user= os.getenv("user") ,         # your PostgreSQL username
        password= os.getenv("password"), # replace with your password
        host= os.getenv("host"),
        port= os.getenv("port")
    )
    return connection