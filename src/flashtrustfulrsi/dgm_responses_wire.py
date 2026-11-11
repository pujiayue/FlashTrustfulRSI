"""Serialize final native DGM Responses envelopes as their original output items."""
from typing import Any


def flatten_response_history(body: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    history = body.get('input')
    if not isinstance(history,list):
        return body,[]
    repaired=[]
    expanded=[]
    for item in history:
        if not isinstance(item,dict) or item.get('object')!='response':
            repaired.append(item)
            continue
        output=item.get('output')
        if (item.get('status')!='completed' or not isinstance(item.get('id'),str)
                or not isinstance(output,list) or not output
                or any(not isinstance(x,dict) or x.get('type') not in ('reasoning','message')
                       for x in output)):
            raise ValueError('Cannot flatten an incomplete or tool-calling response envelope')
        repaired.extend(output)
        expanded.append(item['id'])
    return ({**body,'input':repaired},expanded) if expanded else (body,[])
