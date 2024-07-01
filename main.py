# pylint: disable=global-statement,redefined-outer-name
import argparse
import csv
import glob
import json
import os
from markupsafe import Markup, escape

def nl2br(value):
    escaped_value = escape(value)
    return Markup(escaped_value.replace('\n', Markup('<br>')))

import yaml
from flask import Flask, jsonify, redirect, render_template, send_from_directory, request, url_for
from flask_frozen import Freezer
from flaskext.markdown import Markdown
from elasticsearch import Elasticsearch, helpers

site_data = {}
by_uid = {}

es = Elasticsearch('https://localhost:9200', basic_auth=('elastic', os.getenv('ES_PASSWD')), ssl_assert_fingerprint=os.getenv('ES_FINGERPRINT'))

def main(site_data_path):
    global site_data, extra_files
    extra_files = ["README.md"]
    # Load all for your sitedata one time.
    for f in glob.glob(site_data_path + "/*"):
        extra_files.append(f)
        name, typ = f.split("/")[-1].split(".")
        if typ == "json":
            site_data[name] = json.load(open(f))
        elif typ in {"csv", "tsv"}:
            site_data[name] = list(csv.DictReader(open(f)))
        elif typ == "yml":
            site_data[name] = yaml.load(open(f).read(), Loader=yaml.SafeLoader)

    for typ in ["papers", "speakers", "workshops"]:
        by_uid[typ] = {}
        for p in site_data[typ]:
            by_uid[typ][p["UID"]] = p

    print("Data Successfully Loaded")
    return extra_files

extra_files = main('sitedata')
# ------------- SERVER CODE -------------------->

app = Flask(__name__)
app.jinja_env.add_extension('jinja2.ext.do')
app.jinja_env.filters['nl2br'] = nl2br
app.config.from_object(__name__)
freezer = Freezer(app)
markdown = Markdown(app)


# MAIN PAGES
def _data():
    data = {}
    data["config"] = site_data["config"]
    return data

@app.route("/favicon.ico")
def favicon():
    return send_from_directory('sitedata', "favicon.ico")

# TOP LEVEL PAGES
@app.route("/")
def index():
    data = _data()
    query = request.args.get('query', '')
    page = int(request.args.get('page', 1))
    size = 30
    from_ = (page - 1) * size

    if from_ >= 10000:
        return render_template("error.html", message="You cannot navigate beyond the 10,000th result. Please refine your search by going to earlier pages.")
    if from_+size > 10000:
        size_ = 10000 - from_
    else:
        size_ = size

    # Construct the Elasticsearch query
    filters = {
        "toxic": request.args.get('toxic', ''),
        "redacted": request.args.get('redacted', ''),
        "model": request.args.get('model', ''),
        "hashed_ip": request.args.get('hashed_ip', ''),
        "language": request.args.get('language', ''),
        "country": request.args.get('country', ''),
        "state": request.args.get('state', ''),
        "min_turns": request.args.get('min_turns', '')
    }

    # Build Elasticsearch query
    must_clauses = []
    if query:
        must_clauses.append({
            "nested": {
                "path": "conversation",
                "query": {
                    "match_phrase": {
                        "conversation.content": query
                    }
                }
            }
        })
    if filters['toxic']:
        must_clauses.append({"term": {"toxic": filters['toxic'] == 'true'}})
    if filters['redacted']:
        must_clauses.append({"term": {"redacted": filters['redacted'] == 'true'}})
    if filters['model']:
        must_clauses.append({"term": {"model": filters['model']}})
    if filters['hashed_ip']:
        must_clauses.append({"term": {"hashed_ip": filters['hashed_ip']}})
    if filters['language']:
        must_clauses.append({"term": {"language": filters['language'].title()}})
    if filters['country']:
        must_clauses.append({"term": {"country": filters['country']}})
    if filters['state']:
        must_clauses.append({"term": {"state": filters['state']}})
    if filters['min_turns']:
        must_clauses.append({"range": {"turn": {"gte": int(filters['min_turns'])}}})

    search_query = {
        "query": {
            "bool": {
                "must": must_clauses if must_clauses else {"match_all": {}}
            }
        },
        "from": from_,
        "size": size_
    }

    if must_clauses:
        any_filters = True
    else:
        any_filters = False

    # Execute search query
    response = es.search(index="wildchat", body=search_query)
    conversations = [hit['_source'] for hit in response['hits']['hits']]
    total = response['hits']['total']['value']
    #total_pages = (total // size) + 1
    total_pages = (total + size - 1) // size

    # Pagination logic
    pages = []
    if total_pages > 1:
        if page > 3:
            pages.append(1)
            if page > 4:
                pages.append('...')
        pages.extend(range(max(1, page - 2), min(total_pages + 1, page + 3)))
        if page < total_pages - 3:
            if page < total_pages - 4:
                pages.append('...')
            pages.append(total_pages)
    #import pdb; pdb.set_trace()
    data.update({
        "conversations": conversations,
        "query": query,
        "page": page,
        "pages": pages,
        "total": total,
        "filters": filters,
        "any_filters": any_filters
    })
    return render_template("index.html", **data)

#@app.route("/chat_vis.html")
#def chat_vis():
#    data = _data()
#    return render_template("chat_vis.html", **data)
if __name__ == "__main__":
    debug_val = False
    if os.getenv("FLASK_DEBUG") == "True":
        debug_val = True

    app.run(port=8080, debug=debug_val, extra_files=extra_files, host='0.0.0.0')
