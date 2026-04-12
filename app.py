from flask import Flask, request, jsonify
from engine.pipeline import run_pipeline

app=Flask(__name__)

@app.get("/")
def home():
    return {"status":"ok","system":"ui-reconstruction"}

@app.get("/process")
def process():
    url=request.args.get("url")
    return jsonify(run_pipeline(url))
