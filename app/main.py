
import os, sys
sys.path.append(os.path.dirname(__file__))
from flask import Flask, render_template, jsonify, request
from sigma_module import *
from flask_ngrok import run_with_ngrok

app = Flask(__name__)
run_with_ngrok(app)   
all_yml = read_recursively()
logsources, selections  = get_all(all_yml)
print("Logsources : ",logsources)
print("Selections", selections)
conn = create_db()
add_data(conn, logsources, selections)
@app.route("/")
def home():
  
  print("Launched !")
  return render_template('index.html')
@app.route("/search", methods=['GET'])
def search():
  with sqlite3.connect(os.path.dirname(__file__)+"/db/pythonsqlite.db") as conn:
    terms = search_docs_by_term_in_column(conn, request.args.get("source"),request.args.get("column"), request.args.get("question"))
  response = jsonify(terms)
  response.headers.add('Access-Control-Allow-Origin', '*')
  return response
@app.route("/getDoc")
def getDoc():
  doc = request.args.get("doc")
  if os.path.isfile(doc):
    print("getDoc : Trouvé !")
  else :
    print("getDoc : Not found !")
  with open(doc, "r") as f:
    res = f.read()
  response = jsonify(res)
  response.headers.add('Access-Control-Allow-Origin', '*')
  return response
app.run()
