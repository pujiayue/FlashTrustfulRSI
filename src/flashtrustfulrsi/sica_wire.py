"""Repair native SICA runtime notices interrupting Chat tool-result groups."""
import json


def reconcile_overseer_notifications(messages):
    output = []
    edits = []
    cursor = 0
    while cursor < len(messages):
        message = messages[cursor]
        calls = message.get('tool_calls') or []
        if message['role'] != 'assistant' or not calls:
            output.append(message)
            cursor += 1
            continue
        end = cursor + 1
        while end < len(messages):
            entry = messages[end]
            content = entry.get('content')
            if entry['role'] == 'tool' or (
                entry['role'] == 'user' and isinstance(content, str)
                and content.lstrip().startswith(('<OVERSEER_NOTIFICATION -',
                                                 '<APPLICATION_ERROR -',
                                                 '<APPLICATION_WARNING -'))
            ):
                end += 1
            else:
                break
        entries = messages[cursor+1:end]
        notices = [entry for entry in entries if entry['role']=='user']
        if not notices:
            output.extend(messages[cursor:end])
            cursor = end
            continue
        results = [entry for entry in entries if entry['role']=='tool']
        calls_by_id = {call['id']:call for call in calls}
        result_ids = {result['tool_call_id'] for result in results}
        if len(result_ids)!=len(results) or not result_ids <= calls_by_id.keys():
            raise ValueError('SICA overseer interrupted an inconsistent tool group')
        pending = [call for call in calls if call['id'] not in result_ids]
        if pending or entries != results + notices:
            fixed = dict(message)
            settled = [call for call in calls if call['id'] in result_ids]
            if settled:
                fixed['tool_calls'] = settled
            else:
                fixed.pop('tool_calls',None)
            if pending:
                text = 'The original tool calls below were still pending in the SICA event snapshot. No tool result was available.\n'
                text += json.dumps(pending,ensure_ascii=False,separators=(',',':'))
                fixed['content'] = (fixed.get('content') or '') + '\n' + text
            output.extend([fixed,*results,*notices])
            edits.append({'assistant_index':cursor,'pending_call_ids':[call['id'] for call in pending],
                          'observed_result_ids':[entry['tool_call_id'] for entry in results]})
        else:
            output.extend(messages[cursor:end])
        cursor = end
    return output,edits
