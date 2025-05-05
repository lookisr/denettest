from fastapi import FastAPI
from fastapi import Request
from fastapi import Query
from contextlib import asynccontextmanager
from web3 import AsyncWeb3
from pydantic import BaseModel
from typing import List
import asyncio
import datetime
import aiohttp
from abi import erc20_abi
from config import API_POLYGONSCAN

rpc = "https://polygon-mainnet.g.alchemy.com/v2/1tSM0F50p2q0C26MPwwCMW8AoW1cAoys"
web3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc))
token_address = web3.to_checksum_address("0x1a9b54A3075119f1546C52cA0940551A6ce5d2D0")
token_contract = web3.eth.contract(address=token_address, abi=erc20_abi)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.token_symbol = await token_contract.functions.symbol().call()
    print(f"Token Symbol: {app.state.token_symbol}")
    yield

app = FastAPI(lifespan=lifespan)


class AddressList(BaseModel):
    N: int
    addresses: List[str]


async def check_address_balance(address: str):
    try:
        checksum = web3.to_checksum_address(address)
        balance = await token_contract.functions.getBalance(checksum).call()
        return balance / (10 ** 18)
    except Exception as e:
        print(f"Ошибка при получении баланса {address}: {e}")
        return 0


@app.get("/get_token_info")
async def get_token_info(token_address: str = Query(...)):
    try:
        checksum = web3.to_checksum_address(token_address)
        contract = web3.eth.contract(address=checksum, abi=erc20_abi)

        symbol = await contract.functions.symbol().call()
        name = await contract.functions.name().call()
        total_supply = await contract.functions.totalSupply().call()

        decimals = await contract.functions.decimals().call()
        total_supply_readable = total_supply / (10 ** decimals)

        return {
            "symbol": symbol,
            "name": name,
            "totalSupply": total_supply_readable
        }
    except Exception as e:
        return {"error": str(e)}



@app.post("/get_balance_batch")
async def get_balance_batch(data: AddressList, request: Request):
    tasks = [check_address_balance(addr) for addr in data.addresses]
    balances = await asyncio.gather(*tasks)
    return {"balances": balances, "symbol": request.app.state.token_symbol}


@app.post("/get_top")
async def get_top(data: AddressList):
    n = data.N
    tasks = [check_address_balance(addr) for addr in data.addresses]
    balances = await asyncio.gather(*tasks)
    addresses_with_balances = list(zip(data.addresses, balances))
    sorted_addresses = sorted(addresses_with_balances, key=lambda x: x[1], reverse=True)
    return {"top_addresses": sorted_addresses[:n]}


async def get_last_transaction_date(address: str) -> str:
    try:
        checksum_address = web3.to_checksum_address(address)
        contract_address = "0x1a9b54A3075119f1546C52cA0940551A6ce5d2D0"
        api_key = API_POLYGONSCAN
        topic0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"  # Хэш события Transfer
        padded_address = "0x000000000000000000000000" + checksum_address[2:].lower()

        # Диапазон блоков (последние 24 часа)
        current_block = await web3.eth.block_number
        blocks_per_hour = 3_600 // 2  # ~1,800 блоков в час
        from_block = current_block - (6 * blocks_per_hour)  # ~10,800 блоков за 6 часов
        to_block = "latest"

        # Запрос к Polygonscan API
        url = (
            f"https://api.polygonscan.com/api?module=logs&action=getLogs"
            f"&address={contract_address}"
            f"&fromBlock={from_block}&toBlock={to_block}"
            f"&topic0={topic0}"
            f"&topic0_1_opr=or"
            f"&topic1={padded_address}"  # from
            f"&topic0_2_opr=or"
            f"&topic2={padded_address}"  # to
            f"&apikey={api_key}"
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json()

                if data["status"] != "1" or not data["result"]:
                    print(f"No transactions found for {checksum_address} via Polygonscan")
                    return "No transactions found"

                # Сортируем события по блоку и индексу
                events = data["result"]
                latest_event = max(
                    events,
                    key=lambda e: (int(e["blockNumber"], 16), int(e["transactionIndex"], 16))
                )

                # Получаем информацию о блоке
                block_number = int(latest_event["blockNumber"], 16)
                block = await web3.eth.get_block(block_number)
                timestamp = block["timestamp"]

                # Форматируем дату
                date = datetime.datetime(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                print(f"Last transaction for {checksum_address}: {date}")
                return date

    except Exception as e:
        print(f"Ошибка при получении последней транзакции для {address}: {e}")
        return "Error retrieving transaction date"


@app.post("/get_top_with_transactions")
async def get_top_with_transactions(data: AddressList):
    tasks = [check_address_balance(addr) for addr in data.addresses]
    balances = await asyncio.gather(*tasks)

    # Получаем список адресов с их балансами
    addresses_with_balances = list(zip(data.addresses, balances))

    # Сортируем по балансу в порядке убывания
    sorted_addresses = sorted(addresses_with_balances, key=lambda x: x[1], reverse=True)

    # Получаем дату последней транзакции для каждого адреса
    top_with_transactions = []
    for address, _ in sorted_addresses[:data.N]:
        last_transaction_date = await get_last_transaction_date(address)
        top_with_transactions.append((address, _, last_transaction_date))

    return {"top_addresses_with_transactions": top_with_transactions}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app='main:app', port=8080, reload=True)