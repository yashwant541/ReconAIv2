from __future__ import annotations
from collections import defaultdict
from decimal import Decimal
from hashlib import sha256
from itertools import chain
from rapidfuzz.fuzz import token_set_ratio
from scipy.optimize import linear_sum_assignment
from .config import MatchingConfig
from .domain import Evidence, FinancialDocument, FinancialRecord, Match, MatchStatus, ReconciliationRow, Side, ValidationIssue
from .normalization import currency, date_value, decimal, text
from .ontology import Ontology

ALIASES={"description":("description","narration","particulars","account","name"),"amount":("amount","balance","debit","credit","value"),"date":("date","transaction date","posting date"),"currency":("currency","ccy"),"reference":("reference","invoice","voucher","document no"),"counterparty":("vendor","customer","party","counterparty")}
def _column(fields: dict[str,str], key: str) -> str | None:
    return next((v for k,v in fields.items() if k.casefold() in ALIASES[key]),None)

class ReconciliationPipeline:
    def __init__(self, ontology: Ontology, config: MatchingConfig | None=None) -> None: self.ontology,self.config=ontology,config or MatchingConfig()
    def records(self, documents: tuple[FinancialDocument,...], side: Side) -> tuple[FinancialRecord,...]:
        out=[]
        for table in chain.from_iterable(s.tables for d in documents for s in d.sections):
            for row in table.rows:
                fields={cell.column:text(cell.value) for cell in row.cells}; desc=_column(fields,"description") or next(iter(fields.values()),"")
                amount=decimal(_column(fields,"amount")); cur=currency(_column(fields,"currency") or "") or currency(_column(fields,"amount") or "")
                raw="|".join((side.value,row.provenance.document_id,str(row.provenance.row),desc)); rid=sha256(raw.encode()).hexdigest()[:20]
                out.append(FinancialRecord(rid,side,desc,amount,cur,date_value(_column(fields,"date") or ""),fields.get("Account"),_column(fields,"counterparty"),_column(fields,"reference"),fields,row.provenance,self.ontology.map(desc) or self.ontology.map(fields.get("Account"))))
        return tuple(out)
    def validate(self, records: tuple[FinancialRecord,...]) -> tuple[ValidationIssue,...]:
        issues=[]; seen=set()
        for r in records:
            fingerprint=(r.description.casefold(),r.amount,r.transaction_date,r.reference)
            if fingerprint in seen: issues.append(ValidationIssue("warning","duplicate",r.id,"Duplicate record signature"))
            seen.add(fingerprint)
            if not r.description: issues.append(ValidationIssue("error","missing_description",r.id,"Description is mandatory"))
            if r.amount is None: issues.append(ValidationIssue("warning","missing_amount",r.id,"No parseable amount"))
        return tuple(issues)
    def _candidates(self, left: tuple[FinancialRecord,...], right: tuple[FinancialRecord,...]) -> list[tuple[int,int]]:
        buckets=defaultdict(list)
        for j,r in enumerate(right): buckets[(r.currency,r.concept,r.amount.quantize(Decimal("1")) if r.amount is not None else None)].append(j)
        pairs=[]
        for i,l in enumerate(left):
            key=(l.currency,l.concept,l.amount.quantize(Decimal("1")) if l.amount is not None else None)
            choices=buckets.get(key,[]) or [j for j,r in enumerate(right) if r.currency==l.currency or not l.currency or not r.currency]
            pairs.extend((i,j) for j in choices)
        return pairs
    def _score(self,l: FinancialRecord,r: FinancialRecord) -> Match:
        ev=[]; w=self.config.weights or {}
        if l.amount is not None and r.amount is not None:
            variance=l.amount-r.amount; score=Decimal("1") if abs(variance)<=self.config.tolerance else max(Decimal("0"),Decimal("1")-abs(variance)/max(abs(l.amount),abs(r.amount),Decimal("1")))
            ev.append(Evidence("amount",score,"Amounts within tolerance" if score==1 else "Amount variance detected"))
        else: variance=None
        ratio=Decimal(str(token_set_ratio(l.description,r.description)/100)).quantize(Decimal(".0001")); ev.append(Evidence("text",ratio,"Description similarity"))
        if l.transaction_date and r.transaction_date:
            days=abs((l.transaction_date-r.transaction_date).days); ev.append(Evidence("date",Decimal("1") if days==0 else max(Decimal("0"),Decimal("1")-Decimal(days)/Decimal("31")),f"Date difference: {days} days"))
        if l.concept and l.concept==r.concept: ev.append(Evidence("ontology",Decimal("1"),f"Shared concept: {l.concept}"))
        total=sum((e.score*w.get(e.strategy,Decimal("0")) for e in ev),Decimal("0")); denom=sum((w.get(e.strategy,Decimal("0")) for e in ev),Decimal("0")); confidence=(total/denom if denom else Decimal("0")).quantize(Decimal(".0001"))
        return Match(l.id,r.id,confidence,tuple(ev),variance)
    def reconcile(self,left: tuple[FinancialRecord,...],right: tuple[FinancialRecord,...]) -> tuple[ReconciliationRow,...]:
        scores={(i,j):self._score(left[i],right[j]) for i,j in self._candidates(left,right)}
        assigned=[]
        if scores and self.config.assignment=="hungarian":
            matrix=[[float(1-scores.get((i,j),Match("","",Decimal(0),(),None)).confidence) for j in range(len(right))] for i in range(len(left))]
            rows,cols=linear_sum_assignment(matrix); assigned=[(int(i),int(j)) for i,j in zip(rows,cols) if (int(i),int(j)) in scores]
        else:
            used=set()
            for (i,j),match in sorted(scores.items(),key=lambda x:x[1].confidence,reverse=True):
                if i not in used and j not in used: assigned.append((i,j)); used.add(i);used.add(j)
        result=[]; used_l=set();used_r=set()
        for i,j in assigned:
            m=scores[i,j]
            if m.confidence>=self.config.minimum_confidence:
                used_l.add(i);used_r.add(j); status=MatchStatus.MATCHED if m.confidence>=Decimal(".90") else MatchStatus.PARTIAL
                result.append(ReconciliationRow(status,left[i],right[j],m,"; ".join(e.detail for e in m.evidence)))
        result += [ReconciliationRow(MatchStatus.UNMATCHED,l,None,None,"No qualifying candidate") for i,l in enumerate(left) if i not in used_l]
        result += [ReconciliationRow(MatchStatus.UNMATCHED,None,r,None,"No qualifying candidate") for j,r in enumerate(right) if j not in used_r]
        return tuple(result)
