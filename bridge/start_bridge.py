#!/usr/bin/env python3
import os
import sys
sys.path.insert(0, os.getcwd())

# Load environment
import dotenv
dotenv.load_dotenv()

# Start server
import uvicorn
uvicorn.run(
    "main:app", 
    host="0.0.0.0", 
    port=16000, 
    log_level="info",
    reload=False,
    access_log=True
)
