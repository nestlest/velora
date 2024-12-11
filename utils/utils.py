STABLECOINS = [
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI in Ethereum Mainnet
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC in Ethereum Mainnet
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC2
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC3
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT in Ethereum Mainnet
]

def has_stablecoin(token_pair: dict, stablecoins: list = STABLECOINS) -> bool:
    """Check if the pool has a stablecoin"""
    return token_pair["token0"]["address"] in stablecoins or token_pair["token1"]["address"] in stablecoins

def is_stablecoin(token_address: str, stablecoins: list = STABLECOINS) -> bool:
    return token_address in stablecoins