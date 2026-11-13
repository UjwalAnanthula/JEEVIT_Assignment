# Groups emails by brand and identifies the most recent message in each group.
def group_analyses(items):
    groups={}
    for item in items: groups.setdefault((item.brand or item.email_id).lower(),[]).append(item)
    result=[]
    for key,rows in groups.items():
        rows.sort(key=lambda x:x.email_id)
        for row in rows: row.deal_key=key
        result.append({"deal_key":key,"email_ids":[x.email_id for x in rows],"latest_email_id":rows[-1].email_id,"latest_analysis":rows[-1].model_dump()})
    return result
