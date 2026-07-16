from __future__ import annotations
import base64, json, logging, time
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
from dash import Dash, Input, Output, State, callback, dash_table, dcc, html, no_update
import plotly.express as px
from .config import load_config
from .export import export, rows_data
from .ontology import Ontology
from .parsers import ParserFactory
from .pipeline import ReconciliationPipeline
from .domain import Side

ROOT=Path(__file__).parents[2]; pipeline=ReconciliationPipeline(Ontology.from_yaml(ROOT/"config"/"ontology.yaml"),load_config(ROOT/"config"/"default.yaml")); STORE: dict[str,dict[str,object]]={}
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
def _nav() -> html.Div: return html.Div([dcc.Link(x,href="#"+x.lower().replace(" ","-"),style={"marginRight":"16px"}) for x in ["Dashboard","Upload Center","Document Explorer","Reconciliation Center","Reports"]])
def _decode(contents:str) -> bytes: return base64.b64decode(contents.split(",",1)[1])
def _records_table(records:tuple[object,...]) -> dash_table.DataTable:
    data=[{"id":r.id,"description":r.description,"amount":str(r.amount or ""),"currency":r.currency,"date":str(r.transaction_date or ""),"concept":r.concept,"source":r.provenance.filename} for r in records]
    return dash_table.DataTable(data=data,columns=[{"name":x,"id":x} for x in (list(data[0]) if data else ["id","description","amount","currency","date","concept","source"])],page_size=12,style_table={"overflowX":"auto"},filter_action="native",sort_action="native")
app=Dash(__name__,title="FinanceReconAI",suppress_callback_exceptions=True)
app.layout=html.Main([html.H1("FinanceReconAI"),html.P("Financial document intelligence & explainable reconciliation"),_nav(),dcc.Store(id="session"),html.Hr(),html.Div(id="body")],style={"fontFamily":"Arial","maxWidth":"1400px","margin":"auto"})
@callback(Output("body","children"),Input("session","data"))
def layout(session: str|None):
    if not session: return html.Div([html.H2("Start a reconciliation"),html.P("Upload documents for both sides. Files remain in memory for this browser session."),dcc.Upload(id="left-upload",children=html.Button("Upload LEFT files"),multiple=True),dcc.Upload(id="right-upload",children=html.Button("Upload RIGHT files"),multiple=True),html.Div(id="upload-status")])
    state=STORE.get(session,{"left":(),"right":(),"results":()}); left=state["left"];right=state["right"];results=state["results"]
    figures=px.histogram(x=[float(r.match.confidence) for r in results if r.match],title="Confidence") if results else px.bar(x=["No reconciliation yet"],y=[0],title="Confidence")
    return html.Div([html.H2("Dashboard"),html.Div([html.Div([html.H3("Left records"),html.P(str(len(left)))]),html.Div([html.H3("Right records"),html.P(str(len(right)))]),html.Div([html.H3("Matches"),html.P(str(sum(x.match is not None for x in results)))])],style={"display":"flex","gap":"80px"}),dcc.Graph(figure=figures),html.H2("Upload Center"),dcc.Upload(id="left-upload",children=html.Button("Add LEFT files"),multiple=True),dcc.Upload(id="right-upload",children=html.Button("Add RIGHT files"),multiple=True),html.Div(id="upload-status"),html.H2("Document Explorer / Extraction Review"),html.H3("LEFT"),_records_table(left),html.H3("RIGHT"),_records_table(right),html.H2("Reconciliation Center"),html.Button("Run reconciliation",id="run"),html.Div(id="recon"),html.H2("Reports & Export"),dcc.Dropdown(["xlsx","csv","json"],"xlsx",id="format"),html.Button("Download report",id="download-btn"),dcc.Download(id="download")])
@callback(Output("session","data"),Output("upload-status","children"),Input("left-upload","contents"),Input("right-upload","contents"),State("left-upload","filename"),State("right-upload","filename"),State("session","data"),prevent_initial_call=True)
def upload(left_c,right_c,left_n,right_n,session):
    session=session or str(uuid4()); state=STORE.setdefault(session,{"left":(),"right":(),"results":()}); messages=[]
    for contents,names,side in ((left_c,left_n,Side.LEFT),(right_c,right_n,Side.RIGHT)):
        if contents:
            docs=[]
            for content,name in zip(contents,names):
                try: docs.append(ParserFactory.parse(_decode(content),name))
                except Exception as exc: messages.append(f"{name}: rejected ({type(exc).__name__})")
            records=pipeline.records(tuple(docs),side); key=side.value; state[key]=tuple(state[key])+records; messages.append(f"Accepted {len(docs)} file(s), extracted {len(records)} record(s) on {side.value}.")
    return session," ".join(messages)
@callback(Output("recon","children"),Input("run","n_clicks"),State("session","data"),prevent_initial_call=True)
def reconcile(_:int,session:str|None):
    if not session:return "Upload documents first."
    state=STORE[session]; results=pipeline.reconcile(state["left"],state["right"]);state["results"]=results
    data=rows_data(results); return dash_table.DataTable(data=data,columns=[{"name":x,"id":x} for x in (list(data[0]) if data else ["status"])],page_size=15,style_table={"overflowX":"auto"})
@callback(Output("download","data"),Input("download-btn","n_clicks"),State("format","value"),State("session","data"),prevent_initial_call=True)
def download(_:int,fmt:str,session:str|None):
    if not session:return no_update
    return dcc.send_bytes(export(STORE[session]["results"],fmt),f"finance-reconciliation.{fmt}")
def main() -> None: app.run(debug=False)
if __name__=="__main__": main()
