#!/usr/bin/env python3
"""Dependency-free MCP stdio smoke-test server. Does not call ChatGPT APIs.

This server exposes one tool and one self-contained MCP Apps HTML resource.
It deliberately has no writable state, network access or account credentials.
"""
import json
import sys
from pathlib import Path

# MCP stdio is UTF-8 even when Windows pipes default to a legacy code page.
# Configure before serializing JSON with ensure_ascii=False.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='strict')

NAME = 'chat-launch-smoke'
URI = 'ui://chat-launch-smoke/panel.html'
MIME = 'text/html;profile=mcp-app'
HERE = Path(__file__).resolve().parent


def handle(request):
    method = request.get('method')
    params = request.get('params') or {}
    if method == 'initialize':
        return {
            'protocolVersion': params.get('protocolVersion', '2025-11-25'),
            'capabilities': {'tools': {'listChanged': False}, 'resources': {'listChanged': False}},
            'serverInfo': {'name': NAME, 'version': '0.1.0'},
        }
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': [{
            'name': 'show_new_chat_test',
            'title': 'Создать тестовый чат',
            'description': 'Показывает тестовую MCP App панель. Сам по себе НЕ создаёт чат.',
            'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            '_meta': {
                'ui': {'resourceUri': URI},
                'openai/ui': {'entrypoints': [{'type': 'global'}]},
            },
        }]}
    if method == 'tools/call':
        if params.get('name') != 'show_new_chat_test' or params.get('arguments', {}) != {}:
            raise ValueError('unknown tool or nonempty arguments')
        return {'content': [{'type': 'text', 'text': 'Панель теста готова. Создание чата требует нажатия кнопки пользователем.'}]}
    if method == 'resources/list':
        return {'resources': [{'uri': URI, 'name': 'New Chat Smoke Test', 'mimeType': MIME}]}
    if method == 'resources/read':
        if params.get('uri') != URI:
            raise ValueError('unknown resource')
        return {'contents': [{'uri': URI, 'mimeType': MIME, 'text': (HERE / 'panel.html').read_text(encoding='utf-8')}]}
    raise ValueError('method not implemented')


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                raise ValueError('invalid JSON-RPC envelope')
            if 'id' not in message:  # MCP notifications do not get a response.
                continue
            try:
                result = handle(message)
                response = {'jsonrpc': '2.0', 'id': message['id'], 'result': result}
            except ValueError as exc:
                response = {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': str(exc)}}
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(',', ':')) + '\n')
            sys.stdout.flush()
        except Exception as exc:
            # No sensitive data in errors; never send diagnostics to stdout.
            print('MCP protocol input rejected:', type(exc).__name__, file=sys.stderr)


if __name__ == '__main__':
    main()
