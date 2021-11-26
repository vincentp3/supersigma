import os
import yaml
from pprint import pprint
def read_recursively():
  PATH=os.path.dirname(__file__)+"/sigma-master/rules"
  result = [os.path.join(dp, f) for dp, dn, filenames in os.walk(PATH) for f in filenames if os.path.splitext(f)[1] == '.yml']
  return result
def get_yaml(yaml_file):
  with open(yaml_file, "r") as stream:
      try:
        pass
          #print(yaml.safe_load(stream))
      except yaml.YAMLError as exc:
          print(exc)

def get_logsources(dict_yml,yml_file):
  """Construction d'un logsourcen :
    #fieldName
    #value
    #document
  """
  res=[]
  dict_yml = dict_yml["logsource"]
  myres  = {"category" : "", "product" : "", "service" : "", "document" : yml_file}
  for key in dict_yml.keys():
    myres[key]=dict_yml[key]
    res.append(myres)

  return res

def get_selections(dict_yml, yml_file):
  """Construction d'une sélection :
    #fieldName
    #value
    #document
  """
  res=[]
  dict_yml = dict_yml["detection"]
  for pkey in dict_yml.keys():
    
    if pkey != "condition":
      if type(dict_yml[pkey])==dict :
        for key in dict_yml[pkey].keys():
          res.append({"fieldName" : key, "value" : str(dict_yml[pkey][key]), "document" : yml_file})

  return res

def get_all(all_yml):
  logsources=[]
  selections=[]
  for yml_file in all_yml :
      with open(yml_file, "r") as stream:
        dict_yml = yaml.safe_load(stream)
        logsources+=get_logsources(dict_yml,yml_file)
        selections+=get_selections(dict_yml,yml_file)
  return logsources, selections

import sqlite3



def create_connection(db_file):
    """ create a database connection to a SQLite database """
    conn = None
    try:
        conn = sqlite3.connect(db_file)
    except Exception as e:
        print(e)
    return conn

def create_table(conn, create_table_sql):
    """ create a table from the create_table_sql statement
    :param conn: Connection object
    :param create_table_sql: a CREATE TABLE statement
    :return:
    """
    try:
        c = conn.cursor()
        c.execute(create_table_sql)
    except Exception as e:
        print(e)

def create_selection(conn, selection):
    """
    Create a new task
    :param conn:
    :param task:
    :return:
    """

    sql = ''' INSERT INTO selections(fieldName, value,document)
              VALUES(?,?,?) '''
    cur = conn.cursor()
    cur.execute(sql, selection)
    conn.commit()

    return cur.lastrowid

def create_logsource(conn, logsource):
    """
    Create a new task
    :param conn:
    :param task:
    :return:
    """

    sql = ''' INSERT INTO logsources(category, product,service, document)
              VALUES(?,?,?,?) '''
    cur = conn.cursor()
    cur.execute(sql, logsource)
    conn.commit()

    return cur.lastrowid

def select_all_selections(conn):
    """
    Query all rows in the tasks table
    :param conn: the Connection object
    :return:
    """
    cur = conn.cursor()
    cur.execute("SELECT * FROM selections")

    rows = cur.fetchall()

    for row in rows:
        print(row)

def exec_selection(conn, instr):
  cur = conn.cursor()
  cur.execute(instr)

  rows = cur.fetchall()


  return rows

def select_selections_by_fieldName(conn, fieldName):
    """
    Query all rows in the tasks table
    :param conn: the Connection object
    :return:
    """
    cur = conn.cursor()
    cur.execute("SELECT * FROM selections WHERE fieldName=?",(fieldName,))

    rows = cur.fetchall()

    for row in rows:
        print(row)

def add_data(conn, logsources, selections):
  # create logsources and selections tables
  for logsrc in logsources :
    create_logsource(conn, [logsrc["category"], logsrc["product"], logsrc["service"], logsrc["document"]])
  for selection in selections:
    create_selection(conn, [selection["fieldName"], selection["value"], selection["document"]])

def create_db():
  database = os.path.dirname(__file__)+"/db/pythonsqlite.db"


  if os.path.isfile(database):
    os.remove(database)

  sql_create_logsources_table = """ CREATE TABLE IF NOT EXISTS logsources (
                                      id integer PRIMARY KEY,
                                      category text NOT NULL,
                                      product text,
                                      service text,
                                      document text
                                  ); """

  sql_create_selections_table = """ CREATE TABLE IF NOT EXISTS selections (
                                      id integer PRIMARY KEY,
                                      fieldName text NOT NULL,
                                      value text,
                                      document text
                                  ); """


  
  # create a database connection
  conn = create_connection(database)
  create_table(conn, sql_create_logsources_table)
  create_table(conn, sql_create_selections_table)
  return conn


def search_docs_by_term_in_column(conn, table, column,search_str ):
  if column=="*":
    if table == "logsources":
      columns = ["category", "product", "service"]
    elif table == "selections":
      columns= ["fieldName", "value"]
    part_sql = " OR ".join([a+f" LIKE '%{search_str}%'" for a in columns])
    instr = f"SELECT * FROM {table} WHERE "+part_sql
    print(instr)
  if True :
    instr = f"SELECT {column}, document FROM {table} WHERE {column} LIKE '%{search_str}%'"
  res  = exec_selection(conn, instr)
  return res

def search_docs_by_fixed_table(conn, table_fixed, column_fixed, search_str_fixed, table, column, search_str):
  instr_pre = f"SELECT document FROM {table_fixed} WHERE {column_fixed} LIKE '%{search_str_fixed}%'"
  instr = f"SELECT document FROM {table} WHERE {column} LIKE '%{search_str}%' AND document IN ({instr_pre})"
  res  = exec_selection(conn, instr)
  return [a[0] for a in res]
