from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import aiohttp
import json
import logging
from datetime import datetime
from typing import Optional

from t212_api import CachedTrading212API

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Portfolio AI", description="Trading212 Portfolio with AI Analysis")

# Initialize T212 API client
t212_client = None

def get_t212_client():
    """Initialize T212 client if not already done"""
    global t212_client
    if t212_client is None:
        try:
            t212_client = CachedTrading212API()
            logger.info("Trading212 API client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize T212 client: {e}")
            raise HTTPException(status_code=500, detail="Failed to initialize Trading212 client")
    return t212_client

async def check_inference_service():
    """Check if inference service is available"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://192.168.1.211:11434/api/tags", timeout=5) as response:
                return response.status == 200
    except Exception as e:
        logger.warning(f"Inference service not reachable: {e}")
        return False

def analyse_positions(positions_data, balance_data):
    """pre-compute key portfolio facts so the model doesn't have to"""
    
    positions = sorted(positions_data, key=lambda p: p.get("ppl", 0))
    
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
            "ppl_gbp": biggest_winner.get("ppl")
        } if biggest_winner else None,
        "biggest_loser": {
            "ticker": clean_ticker(biggest_loser.get("ticker", "")),
            "ppl_gbp": biggest_loser.get("ppl")
        } if biggest_loser else None,
    }

async def get_ai_summary(positions_data, balance_data):
    """Get AI summary from Ollama inference service"""
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

        logger.info(f"AI summary prompt: {prompt}")

        payload = {
            "model": "llama3.1:8b",
            "prompt": prompt,
            "stream": False
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://192.168.1.211:11434/api/generate",
                json=payload,
                timeout=120
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("response", "").strip()
                else:
                    logger.error(f"Inference API error: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error getting AI summary: {e}")
        return None

@app.get("/summary")
async def get_portfolio_summary():
    """Get portfolio summary with AI analysis"""
    try:
        # Get T212 client
        client = get_t212_client()
        
        # Fetch portfolio data from T212 API
        logger.info("Fetching portfolio data from Trading212")
        portfolio_data = client.get_all_data()
        
        # Extract required fields
        cash_data = portfolio_data.get("cash", {})
        positions = portfolio_data.get("portfolio", [])
        
        total = cash_data.get("total", 0.0)
        invested = cash_data.get("invested", 0.0)
        unrealised_pnl = cash_data.get("ppl", 0.0)
        cash = cash_data.get("free", 0.0)
        
        # Calculate unrealised percentage
        unrealised_pct = (unrealised_pnl / invested * 100) if invested > 0 else 0.0
        
        # Count positions
        position_count = len(positions)
        
        # Check if inference service is available
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
        
    except Exception as e:
        logger.error(f"Error generating portfolio summary: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test T212 client
        client = get_t212_client()
        
        # Test inference service
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