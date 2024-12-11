import numpy as np
import pandas as pd
from pandas import DataFrame
import joblib

from ta.trend import MACD
from ta.momentum import RSIIndicator, ROCIndicator

from sklearn.preprocessing import MinMaxScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout

from db.miner_db import MinerDBManager

PREDICTION_COUNT = 6

db_manager = MinerDBManager()

def load_datasets_from_db(pool_address):
    pool_address = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'
    input = pd.read_sql(f"select * from token_metrics where token_address='{pool_address}'", db_manager.engine)
    
    return input

def extract_features(input):
    input['SMA_50'] = input['close_price'].rolling(window=50).mean()
    input['SMA_200'] = input['close_price'].rolling(window=200).mean()
    input['RSI'] = RSIIndicator(input['close_price']).rsi()
    input['Momentum'] = ROCIndicator(input['close_price']).roc()
    input['MACD'] = MACD(input['close_price']).macd()
    
    for i in range(1, 1 + PREDICTION_COUNT):
        input[f'NextPrice{i}'] = input['close_price'].shift(-1 * i)

    input.replace([np.inf, -np.inf], np.nan, inplace = True)        
    input.dropna(inplace = True)
    print(input)
    
    return input

def preprocess(dataset: DataFrame):
    model_path = './base_model'
    
    X = dataset[['close_price', 'SMA_50', 'SMA_200', 'RSI', 'Momentum', 'MACD']].values
    y = dataset[['NextPrice1', 'NextPrice2', 'NextPrice3', 'NextPrice4', 'NextPrice5', 'NextPrice6']].values
    
    X_scaler = joblib.load(f'{model_path}/X_scaler.pkl')
    y_scaler = joblib.load(f'{model_path}/y_scaler.pkl')
    X_scaled = X_scaler.transform(X)
    y_scaled = y_scaler.transform(y)
    
    return X_scaler, y_scaler, X_scaled, y_scaled

def predict(X, y_scaler):
    model_path = './base_model'
    
    X = X.reshape(X.shape[0], 1, X.shape[1])
    
    model = load_model(f'{model_path}/lstm_model.h5')
    
    predicted_prices = model.predict(X)
    predicted_prices = y_scaler.inverse_transform(predicted_prices)
    
    print('-------------------------------------------')
    print(predicted_prices)
    
    return predicted_prices

def predict_token_price(data: DataFrame = None, pool_address: str = None):
    if data is None and pool_address is None:
        print('No data available.')
        return None
    
    if data is None:
        data = load_datasets_from_db(pool_address)
    
    data = extract_features(data)
    X_scaler, y_scaler, X, y = preprocess(data)
    result = predict(X, y_scaler)
    
    return result

if __name__ == '__main__':
    dataset = load_datasets_from_db()
    dataset = extract_features(dataset)
    X_scaler, y_scaler, X, y = preprocess(dataset)
    mse_loss = predict(X, y_scaler)