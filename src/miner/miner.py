from communex.module.module import Module, endpoint
from communex.key import generate_keypair
from communex.compat.key import classic_load_key
from keylimiter import TokenBucketLimiter

import os
import json
import hashlib
import pandas as pd
from datetime import datetime, timezone
from uniswap_fetcher_rs import UniswapFetcher
from typing import List

from utils.helpers import unsigned_hex_to_int, signed_hex_to_int
from utils.protocols import *
from utils.log import log
from utils.bfs import breadthFirstSearch
from src.miner.predict_lstm_model import predict_token_price
from db.miner_db import MinerDBManager

START_TIMESTAMP = int(datetime(2021, 5, 4).replace(tzinfo=timezone.utc).timestamp())
DAY = 60 * 60 * 24

class Miner(Module):
    """
    A module class for mining and generating responses to prompts.

    Attributes:
        None

    Methods:
        generate: Generates a response to a given prompt using a specified model.
    """
    def __init__(self) -> None:
        super().__init__()
        
        self.uniswap_fetcher_rs = UniswapFetcher(os.getenv('ETHEREUM_RPC_NODE_URL'))
        self.db_manager = MinerDBManager()
        
        self.last_synced_time = self.db_manager.lastSyncedTimestamp()
        if self.last_synced_time is None:
            self.last_synced_time = START_TIMESTAMP
        self.sync_token_pairs()
    
    def sync_token_pairs(self) -> None:
        log('Syncing token pairs...')
        
        now = int(datetime.now().timestamp() - 12)
        token_pairs = self.uniswap_fetcher_rs.get_pool_created_events_between_two_timestamps(self.last_synced_time, now)
        self.db_manager.add_token_pairs(token_pairs, now)
        self.last_synced_time = now
        
        log(f'Sync finished until {now}')

    @endpoint
    def forwardHealthCheckSynapse(self, synapse: dict):
        time_completed = self.db_manager.fetch_completed_time()['end']
        token_pairs = self.db_manager.fetch_token_pairs()
        pool_addresses = [token_pair['pool_address'] for token_pair in token_pairs]
        # print(f'HealthCheckResponse returned: {time_completed}, {pool_addresses}')
        
        return HealthCheckResponse(time_completed = time_completed, pool_addresses = pool_addresses).json()
        
    @endpoint
    def forwardPoolEventSynapse(self, synapse: dict):
        synapse = PoolEventSynapse(**synapse)
        # Generate a response from scraping the rpc server
        block_number_start, block_number_end = self.uniswap_fetcher_rs.get_block_number_range(synapse.start_datetime, synapse.end_datetime)
        pool_events = self.db_manager.fetch_pool_events(block_number_start, block_number_end)
        pool_events_dict = [pool_event.to_dict() for pool_event in pool_events]
        
        pool_evnets_string = json.dumps(pool_events_dict)
        hash_object = hashlib.sha256(pool_evnets_string.encode())  # Convert string to bytes
        hash_hex = hash_object.hexdigest()  # Get the hash as a hexadecimal string
        
        return PoolEventResponse(data = pool_events_dict, overall_data_hash = hash_hex).json()
    
    @endpoint
    def forwardPoolMetricSynapse(self, synapse: dict):
        synapse = PoolMetricSynapse(**synapse)
        pool_metric = self.db_manager.find_pool_metric_timetable_pool_address(synapse.timestamp, synapse.pool_address, synapse.interval)
        print(f'pool_metric found: {pool_metric}')
        print(f'pool_metric jsonified: {PoolMetricResponse(**pool_metric).json()}')
        return PoolMetricResponse(**pool_metric).json()
    
    @endpoint
    def forwardPredictionSynapse(self, synapse: PredictionSynapse) -> str:
        synapse = PredictionSynapse(**synapse)
        self.sync_token_pairs()
        token_pairs = breadthFirstSearch(self, synapse.token_address)
        price_in_usd = [1] * (12 * 24 - 6)
        for token_pair in token_pairs:
            pool_address = self.db_manager.search_pool_address(token_pair[0], token_pair[1])
            data = self.uniswap_fetcher_rs.get_pool_price_ratios(pool_address, synapse.timestamp - DAY, synapse.timestamp - 30 * 60, 300)
            price_in_usd = [price_in_usd[i] * float(data[i]['price_ratio']) for i in range(len(data))]
        
        price_history = pd.DataFrame(price_in_usd, columns=['close_price'])
        prices = predict_token_price(price_history)
        prices = prices.tolist()
        print(f"prices: {prices}")
        return PredictionResponse(prices=prices).json()
    
    @endpoint
    def forwardCurrentPoolMetricSynapse(self, synapse: CurrentPoolMetricSynapse):
        synapse = CurrentPoolMetricSynapse(**synapse)
        db_data = self.db_manager.fetch_current_pool_metrics(synapse.page_limit, synapse.page_number, synapse.search_query, synapse.sort_by, synapse.sort_order)
        pool_metrics = db_data['pool_metrics']
        total_pool_count = db_data['total_pool_count']
        print(f'current_pool_metrics: {pool_metrics}')
        data =  [CurrentPoolMetric(
            pool_address=current_pool_metric["pool_address"],
            liquidity_token0=current_pool_metric["liquidity_token0"],
            liquidity_token1=current_pool_metric["liquidity_token1"],
            total_volume_token0=current_pool_metric["total_volume_token0"],
            total_volume_token1=current_pool_metric["total_volume_token1"],
            volume_token0_1day=current_pool_metric["volume_token0_1day"],
            volume_token1_1day=current_pool_metric["volume_token1_1day"],
            fee=current_pool_metric["fee"],
            token0_symbol=current_pool_metric["token0_symbol"],
            token1_symbol=current_pool_metric["token1_symbol"],
            token0_price=current_pool_metric["token0_price"],
            token1_price=current_pool_metric["token1_price"],
            ) for current_pool_metric in pool_metrics]
        return CurrentPoolMetricResponse(data = data, overall_data_hash = "", total_pool_count=total_pool_count).json()
    
    @endpoint
    def forwardRecentPoolEventSynapse(self, synapse: RecentPoolEventSynapse):
        synapse = RecentPoolEventSynapse(**synapse)
        pool_events = self.db_manager.fetch_recent_pool_events(synapse.page_limit, synapse.filter_by)
        print(f'pool_events: {pool_events}')
        pool_events_dict = [
            PoolEvent(
                timestamp=timestamp,
                pool_address=pool_address,
                token0_symbol=token0_symbol,
                token1_symbol=token1_symbol,
                amount0=float(signed_hex_to_int(amount0)) / 10 ** token0_decimals if event_type == 'swap' else float(unsigned_hex_to_int(amount0)) / 10 ** token0_decimals,
                amount1=float(signed_hex_to_int(amount1)) / 10 ** token1_decimals if event_type == 'swap' else float(unsigned_hex_to_int(amount1)) / 10 ** token1_decimals,
                event_type=event_type,
                transaction_hash=transaction_hash
            )
            for timestamp, pool_address, token0_symbol, token1_symbol, token0_decimals, token1_decimals, amount0, amount1, transaction_hash, event_type in pool_events]
        # print(f'pool_events_dict: {pool_events_dict}')
        return RecentPoolEventResponse(data = pool_events_dict, overall_data_hash = "").json()
    @endpoint
    def forwardCurrentTokenMetricSynapse(self, synapse: CurrentTokenMetricSynapse):
        synapse = CurrentTokenMetricSynapse(**synapse)
        db_data = self.db_manager.fetch_current_token_metrics(synapse.page_limit, synapse.page_number, synapse.search_query, synapse.sort_by)
        token_metrics = db_data['token_metrics']
        total_token_count = db_data['total_token_count']
        print(f'token_metrics: {token_metrics}')
        
        data = [CurrentTokenMetric(
            token_address=token_metric.token_address,
            symbol=token_metric.symbol,
            price=token_metric.price,
            total_volume=token_metric.total_volume,
            total_liquidity=token_metric.total_liquidity
            ) for token_metric in token_metrics]
        return CurrentTokenMetricResponse(data = data, total_token_count = total_token_count).json()
    
    @endpoint
    def forwardPoolMetricAPISynapse(self, synapse: PoolMetricAPISynapse):
        synapse = PoolMetricAPISynapse(**synapse)
        db_data = self.db_manager.fetch_pool_metric_api(synapse.page_limit, synapse.page_number, synapse.pool_address, synapse.interval, synapse.period, synapse.start_timestamp, synapse.end_timestamp)
        pool_metrics = db_data['pool_metrics']
        total_pool_count = db_data['total_pool_count']
        token_pair_info = db_data['token_pair_info']
        print(f"token_pair_info: {token_pair_info}")
        token_pair_data = TokenPairData(
            token0_price=token_pair_info.token0_price,
            token1_price=token_pair_info.token1_price,
            token0_address=token_pair_info.token0_address,
            token1_address=token_pair_info.token1_address,
            token0_symbol=token_pair_info.token0_symbol,
            token1_symbol=token_pair_info.token1_symbol,
            fee=token_pair_info.fee,
            pool_address=token_pair_info.pool_address,
        )
        data = [PoolMetricAPI(
            timestamp=pool_metric.timestamp,
            price=pool_metric.price,
            liquidity_token0=pool_metric.liquidity_token0,
            liquidity_token1=pool_metric.liquidity_token1,
            volume_token0=pool_metric.volume_token0,
            volume_token1=pool_metric.volume_token1,
            ) for pool_metric in pool_metrics]
        print(f"total_pool_count: {total_pool_count}")
        return PoolMetricAPIResponse(data = data, token_pair_data=token_pair_data, total_pool_count = total_pool_count).json()
    
    @endpoint
    def forwardTokenMetricAPISynapse(self, synapse: TokenMetricAPISynapse):
        synapse = TokenMetricAPISynapse(**synapse)
        db_data = self.db_manager.fetch_token_metric_api(synapse.page_limit, synapse.page_number, synapse.token_address, synapse.interval, synapse.period, synapse.start_timestamp, synapse.end_timestamp)
        token_metrics = db_data['token_metrics']
        total_token_count = db_data['total_token_count']
        token_data = db_data['token_data']
        print(f"token_data: {token_data}")
        token_data = TokenData(
            token_address=token_data.address,
            symbol=token_data.symbol,
            decimals=token_data.decimals,
        )
        data = [TokenMetricAPI(
            timestamp=token_metric.timestamp,
            close_price=token_metric.close_price,
            high_price=token_metric.high_price,
            low_price=token_metric.low_price,
            total_volume=token_metric.total_volume,
            total_liquidity=token_metric.total_liquidity,
            ) for token_metric in token_metrics]
        print(f"total_token_count: {total_token_count}")
        return TokenMetricAPIResponse(data = data, token_data=token_data, total_token_count = total_token_count).json()
    
    @endpoint
    def forwardSwapEventAPISynapse(self, synapse: SwapEventAPISynapse):
        synapse = SwapEventAPISynapse(**synapse)
        db_data = self.db_manager.fetch_swap_event_api(synapse.page_limit, synapse.page_number, synapse.pool_address, synapse.start_timestamp, synapse.end_timestamp)
        pool_events = db_data['swap_events']
        total_swap_count = db_data['total_swap_count']
        data = [{
            "timestamp": pool_event.timestamp,
            "pool_address": pool_event.pool_address,
            "block_number": pool_event.block_number,
            "transaction_hash": pool_event.transaction_hash,
            "sender": pool_event.sender,
            "to": pool_event.to,
            "amount0": pool_event.amount0,
            "amount1": pool_event.amount1,
            "sqrt_price_x96": pool_event.sqrt_price_x96,
            "liquidity": pool_event.liquidity,
            "tick": pool_event.tick,
            } for pool_event in pool_events]
        return SwapEventAPIResponse(data = data, total_event_count = total_swap_count).json()
    @endpoint
    def forwardMintEventAPISynapse(self, synapse: MintEventAPISynapse):
        synapse = MintEventAPISynapse(**synapse)
        db_data = self.db_manager.fetch_mint_event_api(synapse.page_limit, synapse.page_number, synapse.pool_address, synapse.start_timestamp, synapse.end_timestamp)
        pool_events = db_data['mint_events']
        total_mint_count = db_data['total_mint_count']
        data = [{
            "timestamp": pool_event.timestamp,
            "pool_address": pool_event.pool_address,
            "block_number": pool_event.block_number,
            "transaction_hash": pool_event.transaction_hash,
            "sender": pool_event.sender,
            "owner": pool_event.owner,
            "tick_lower": pool_event.tick_lower,
            "tick_upper": pool_event.tick_upper,
            "amount": pool_event.amount,
            "amount0": pool_event.amount0,
            "amount1": pool_event.amount1,
            } for pool_event in pool_events]
        return MintEventAPIResponse(data = data, total_event_count = total_mint_count).json()
    @endpoint
    def forwardBurnEventAPISynapse(self, synapse: BurnEventAPISynapse):
        synapse = BurnEventAPISynapse(**synapse)
        print(f"synapse: {synapse}")
        db_data = self.db_manager.fetch_burn_event_api(synapse.page_limit, synapse.page_number, synapse.pool_address, synapse.start_timestamp, synapse.end_timestamp)
        pool_events = db_data['burn_events']
        total_burn_count = db_data['total_burn_count']
        data = [{
            "timestamp": pool_event.timestamp,
            "pool_address": pool_event.pool_address,
            "block_number": pool_event.block_number,
            "transaction_hash": pool_event.transaction_hash,
            "owner": pool_event.owner,
            "tick_lower": pool_event.tick_lower,
            "tick_upper": pool_event.tick_upper,
            "amount": pool_event.amount,
            "amount0": pool_event.amount0,
            "amount1": pool_event.amount1,
            
            } for pool_event in pool_events]
        return BurnEventAPIResponse(data = data, total_event_count = total_burn_count).json()
    
    @endpoint
    def forwardPredictionAPISynapse(self, synapse: PredictionAPISynapse) -> str:
        synapse = PredictionAPISynapse(**synapse)
        self.sync_token_pairs()
        token_pairs = breadthFirstSearch(self, synapse.token_address)
        price_in_usd = [1] * (12 * 24)
        for token_pair in token_pairs:
            pool_address = self.db_manager.search_pool_address(token_pair[0], token_pair[1])
            data = self.uniswap_fetcher_rs.get_pool_price_ratios(pool_address, synapse.timestamp - DAY, synapse.timestamp, 300)
            price_in_usd = [price_in_usd[i] * float(data[i]["price_ratio"]) for i in range(len(data)) if i < 12 * 24]
        
        price_history = pd.DataFrame(price_in_usd, columns=['close_price'])
        predicted_prices = predict_token_price(price_history)
        predicted_prices = predicted_prices.tolist()
        predicted_data = [ {"timestamp": synapse.timestamp + i * 300, "price": predicted_prices[i]} for i in range(len(predicted_prices))]
        historical_data = [ {"timestamp": synapse.timestamp - DAY + i * 300, "price": price_in_usd[i]} for i in range(len(price_in_usd))][-10:]
        token_symbol = self.db_manager.get_token_info(synapse.token_address).symbol
        return PredictionAPIResponse( historical_data=historical_data, predicted_data=predicted_data, token_symbol=token_symbol).json()

if __name__ == "__main__":
    """
    Example
    """
    from communex.module.server import ModuleServer
    import uvicorn

    key = classic_load_key("your_key_here")
    miner = Miner()
    refill_rate = 1 / 400
    # Implementing custom limit
    bucket = TokenBucketLimiter(20, refill_rate)
    server = ModuleServer(miner, key, limiter=bucket, subnets_whitelist=[30], use_testnet=False)
    app = server.get_fastapi_app()
    # token0 = "0xaea46a60368a7bd060eec7df8cba43b7ef41ad85"
    # token1 = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    # start_datetime = "2024-09-27 11:24:56"
    # end_datetime = "2024-09-27 15:25:56"
    # interval = "1h"
    # print(uniswap_fetcher_rs.fetch_pool_data_py(token0, token1, start_datetime, end_datetime, interval))

    # Only allow local connections
    uvicorn.run(app, host="0.0.0.0", port=9900)
