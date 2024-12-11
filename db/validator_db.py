from sqlalchemy import create_engine, Column, String, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from typing import List
from utils.config import get_postgres_validator_url

# Define the base class for your table models
Base = declarative_base()
class BaseTable(Base):
    __abstract__ = True
    
    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}

class TokenTable(BaseTable):
    __tablename__ = 'tokens'
    token_address = Column(String, primary_key = True, nullable = False)
    last_synced_time = Column(Integer, nullable=False)

class ValidatorDBManager:
    def __init__(self, url = get_postgres_validator_url()):
        # Create the SQLAlchemy engine
        self.engine = create_engine(url)

        # Create a configured "Session" class
        self.Session = sessionmaker(bind=self.engine)
        
        Base.metadata.create_all(self.engine)
            
    def lastSyncedTimeStamp(self):
        with self.Session() as session:
            res = session.query(TokenTable).order_by(TokenTable.last_synced_time.desc()).first()
            if res is not None:
                return res.last_synced_time
    
    def add_tokens(self, token_infos: List[str], timestamp: int):
        with self.Session() as session:
            data = [TokenTable(token_address = token_info, last_synced_time = timestamp) for token_info in token_infos]
            session.add_all(data)
            session.commit()
    
    def getAvailableTokens(self):
        with self.Session() as session:
            res = session.query(TokenTable).all()
            return [token.token_address for token in res]