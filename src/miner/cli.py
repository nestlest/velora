import typer
import getpass
from typing import Annotated
from communex.compat.key import classic_load_key
from keylimiter import TokenBucketLimiter
from communex.module.server import ModuleServer
import uvicorn
import os
from dotenv import load_dotenv

from src.miner.miner import Miner

load_dotenv()

app = typer.Typer()

@app.command("serve-subnet")
def serve(
    commune_key: str,
    netuid: int = 30,
    ip: str = typer.Option("0.0.0.0", help="IP to bind the server to"),
    port: int = typer.Option(9900, help="Port to bind the server to"),
    use_testnet: bool = typer.Option(False, help="Network to connect to [`mainnet`, `testnet`]"),
    call_timeout: int = typer.Option(65, help="Timeout for the call"),
):
    # password = getpass.getpass(prompt="Enter the password for your key:")
    key = classic_load_key(commune_key)
    miner = Miner()
    refill_rate = 1
    # Implementing custom limit
    bucket = TokenBucketLimiter(50, refill_rate)
    server = ModuleServer(miner, key, limiter=bucket, subnets_whitelist=[netuid], use_testnet = use_testnet)
    app = server.get_fastapi_app()

    # Only allow local connections
    uvicorn.run(app, host=ip, port=port)

if __name__ == "__main__":
    typer.run(serve)