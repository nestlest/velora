import numpy as np
import pandas as pd
from pandas import DataFrame
import joblib

from ta.trend import MACD
from ta.momentum import RSIIndicator, ROCIndicator

from sklearn.preprocessing import MinMaxScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout, Input

from db.miner_db import MinerDBManager

PREDICTION_COUNT = 6

db_manager = MinerDBManager()

def load_datasets_from_db():
    pool_address = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'
    input = pd.read_sql(f"select * from token_metrics where token_address='{pool_address}'", db_manager.engine)
    
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
    X = dataset[['close_price', 'SMA_50', 'SMA_200', 'RSI', 'Momentum', 'MACD']].values
    y = dataset[['NextPrice1', 'NextPrice2', 'NextPrice3', 'NextPrice4', 'NextPrice5', 'NextPrice6']].values
    
    X_scaler = MinMaxScaler(feature_range=(0, 1))
    y_scaler = MinMaxScaler(feature_range=(0, 1))
    X_scaled = X_scaler.fit_transform(X)
    y_scaled = y_scaler.fit_transform(y)
    
    return X_scaler, y_scaler, X_scaled, y_scaled

def base_lstm_model(X, y):
    model = Sequential()
    model.add(Input(shape=(X.shape[1], X.shape[2])))
    model.add(LSTM(units=50, return_sequences=True))
    model.add(Dropout(0.2))
    model.add(LSTM(units=50, return_sequences=False))
    model.add(Dropout(0.2))
    model.add(Dense(units=y.shape[1]))
    
    model.compile(optimizer='adam', loss='mean_squared_error')
    
    return model

def train(X_scaler, y_scaler, X, y):
    model_path = './base_model'
    
    X = X.reshape(X.shape[0], 1, X.shape[1])
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size = 0.2)
    
    model = base_lstm_model(X_train, y_train)
    
    model.fit(X_train, y_train, epochs=100, batch_size=32)
    model.save(f'{model_path}/lstm_model.h5')
    joblib.dump(X_scaler, f'{model_path}/X_scaler.pkl')
    joblib.dump(y_scaler, f'{model_path}/y_scaler.pkl')
    
    predicted_prices = model.predict(X_test)
    
    predicted_prices = y_scaler.inverse_transform(predicted_prices)
    y_test_rescaled = y_scaler.inverse_transform(y_test.reshape(-1, 6))
    
    mse = mean_squared_error(y_test_rescaled, predicted_prices)
    print(f'Mean Squared Error: {mse}')
    print('-------------------------------------------')
    print(y_test_rescaled)
    print('********************************************')
    print(predicted_prices)
    
    return mse

if __name__ == '__main__':
    dataset = load_datasets_from_db()
    X_scaler, y_scaler, X, y = preprocess(dataset)
    mse_loss = train(X_scaler, y_scaler, X, y)