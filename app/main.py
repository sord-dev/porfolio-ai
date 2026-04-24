from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import aiohttp
import asyncio
import logging
import time
import json
from datetime import datetime
from typing import Optional

from t212_api import CachedTrading212API

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Portfolio AI", description="Trading212 Portfolio with AI Analysis")

# Load configuration
def load_config():
    try:
        with open('/app/trading212_config.json', 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        raise

config = load_config()

# Initialize T212 API client
t212_client = None

# --- request coalescing state ---
_summary_lock = asyncio.Lock()
_summary_in_flight: Optional[asyncio.Event] = None
_summary_cached_result: Optional[dict] = None
_summary_cache_ts: float = 0.0
_SUMMARY_COALESCE_TTL: float = 30.0

# --- inference service availability cache ---
_inference_cache: Optional[bool] = None
_inference_cache_ts: float = 0.0
_INFERENCE_CACHE_TTL: float = 45.0

# --- AI summary cache ---
_ai_summary_cache: Optional[str] = None
_ai_summary_cache_key: Optional[tuple] = None
_ai_summary_cache_ts: float = 0.0
_AI_SUMMARY_TTL: float = 300.0

def get_t212_client():
    global t212_client
    if t212_client is None:
        try:
            t212_client = CachedTrading212API()
            logger.info("Trading212 API client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize T212 client: {e}")
            raise HTTPException(status_code=500, detail="Failed to initialize Trading212 client")
    return t212_client

async def check_inference_service() -> bool:
    global _inference_cache, _inference_cache_ts
    now = time.monotonic()
    if _inference_cache is not None and (now - _inference_cache_ts) < _INFERENCE_CACHE_TTL:
        return _inference_cache
    try:
        inference_config = config.get('inference_service', {})
        base_url = inference_config.get('base_url', 'http://localhost:11434')
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/api/tags", timeout=5) as response:
                result = response.status == 200
    except Exception as e:
        logger.warning(f"Inference service not reachable: {e}")
        result = False
    _inference_cache = result
    _inference_cache_ts = now
    return result

def analyse_positions(positions_data, balance_data):
    """pre-compute key portfolio facts so the model doesn't have to"""

    positions = sorted(positions_data, key=lambda p: p.get("ppl_gbp", 0))

    biggest_loser = positions[0] if positions else None
    biggest_winner = positions[-1] if positions else None

    def clean_ticker(t):
        for suffix in ["_US_EQ", "_UK_EQ", "l_EQ", "L_EQ", "_EQ"]:
            t = t.replace(suffix, "")
        return t

    total = balance_data.get("total", 0)
    invested = balance_data.get("invested", 0)
    pnl = balance_data.get("ppl", 0)
    pct = (pnl / invested * 100) if invested > 0 else 0
    cash = balance_data.get("free", 0)

    return {
        "total_gbp": round(total, 2),
        "invested_gbp": round(invested, 2),
        "unrealised_pnl_gbp": round(pnl, 2),
        "unrealised_pct": round(pct, 2),
        "cash_gbp": round(cash, 2),
        "position_count": len(positions),
        "biggest_winner": {
            "ticker": clean_ticker(biggest_winner.get("ticker", "")),
            "ppl_gbp": biggest_winner.get("ppl_gbp")
        } if biggest_winner else None,
        "biggest_loser": {
            "ticker": clean_ticker(biggest_loser.get("ticker", "")),
            "ppl_gbp": biggest_loser.get("ppl_gbp")
        } if biggest_loser else None,
    }

async def get_ai_summary(positions_data, balance_data):
    global _ai_summary_cache, _ai_summary_cache_key, _ai_summary_cache_ts

    cache_key = (
        round(balance_data.get("total", 0), 2),
        round(balance_data.get("ppl", 0), 2),
        round(balance_data.get("free", 0), 2),
    )
    now = time.monotonic()
    if (
        _ai_summary_cache is not None
        and _ai_summary_cache_key == cache_key
        and (now - _ai_summary_cache_ts) < _AI_SUMMARY_TTL
    ):
        logger.info("Returning cached AI summary")
        return _ai_summary_cache

    try:
        facts = analyse_positions(positions_data, balance_data)

        prompt = f"""you are a terse portfolio assistant.
respond in plain text only. no markdown. no asterisks. no sections.
2-3 sentences. cover: overall health, biggest mover, one thing worth watching.

portfolio facts:
- total value: £{facts['total_gbp']}
- unrealised P&L: £{facts['unrealised_pnl_gbp']} ({facts['unrealised_pct']}%)
- cash available: £{facts['cash_gbp']}
- positions: {facts['position_count']}
- biggest winner: {facts['biggest_winner']['ticker']} (+£{facts['biggest_winner']['ppl_gbp']})
- biggest loser: {facts['biggest_loser']['ticker']} (£{facts['biggest_loser']['ppl_gbp']})
"""

        inference_config = config.get('inference_service', {})
        base_url = inference_config.get('base_url', 'http://localhost:11434')
        model = inference_config.get('model', 'llama3.1:8b')
        timeout = inference_config.get('timeout', 120)
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/api/generate",
                json=payload,
                timeout=timeout
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    text = result.get("response", "").strip()
                    _ai_summary_cache = text
                    _ai_summary_cache_key = cache_key
                    _ai_summary_cache_ts = time.monotonic()
                    return text
                else:
                    logger.error(f"Inference API error: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error getting AI summary: {e}")
        return None

async def _do_fetch_summary() -> dict:
    client = get_t212_client()

    logger.info("Fetching portfolio data from Trading212")
    portfolio_data = client.get_all_data()

    cash_data = portfolio_data.get("cash", {})
    positions = portfolio_data.get("portfolio", [])

    total = cash_data.get("total", 0.0)
    invested = cash_data.get("invested", 0.0)
    unrealised_pnl = cash_data.get("ppl", 0.0)
    cash = cash_data.get("free", 0.0)

    unrealised_pct = (unrealised_pnl / invested * 100) if invested > 0 else 0.0
    position_count = len(positions)

    inference_available = await check_inference_service()
    ai_summary = None

    slim_positions = [
        {
            "ticker": p.get("ticker", "").replace("_US_EQ","").replace("_EQ","").replace("l_EQ","").replace("_EQ",""),
            "ppl_gbp": p.get("ppl"),
            "current_value_gbp": p.get("currentValue")
        }
        for p in positions
    ]

    if inference_available:
        logger.info("Getting AI summary")
        ai_summary = await get_ai_summary(slim_positions, cash_data)

    response_data = {
        "total": round(total, 2),
        "invested": round(invested, 2),
        "unrealised_pnl": round(unrealised_pnl, 2),
        "unrealised_pct": round(unrealised_pct, 2),
        "cash": round(cash, 2),
        "positions": position_count,
        "ai_summary": ai_summary,
        "inference_available": inference_available
    }

    logger.info(f"Portfolio summary generated successfully. Total: £{total:.2f}")
    return response_data

@app.get("/summary")
async def get_portfolio_summary():
    global _summary_in_flight, _summary_cached_result, _summary_cache_ts

    async with _summary_lock:
        now = time.monotonic()
        if _summary_cached_result is not None and (now - _summary_cache_ts) < _SUMMARY_COALESCE_TTL:
            logger.info("Returning coalesced /summary result (cached)")
            return _summary_cached_result
        if _summary_in_flight is not None:
            event = _summary_in_flight
        else:
            event = None

    if event is not None:
        logger.info("Waiting for in-flight /summary fetch")
        await event.wait()
        return _summary_cached_result

    my_event = asyncio.Event()
    async with _summary_lock:
        _summary_in_flight = my_event

    result = None
    try:
        result = await _do_fetch_summary()
    except Exception as e:
        logger.error(f"Error generating portfolio summary: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        async with _summary_lock:
            _summary_in_flight = None
            if result is not None:
                _summary_cached_result = result
                _summary_cache_ts = time.monotonic()
        my_event.set()

    return result

@app.get("/health")
async def health_check():
    try:
        client = get_t212_client()

        inference_available = await check_inference_service()

        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "services": {
                "t212_api": "connected",
                "inference_service": "connected" if inference_available else "disconnected"
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e)
            }
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
