"""Python 3.11+, standard library only. Local single-user demonstration."""
import argparse
import json
import mimetypes
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import engine
from live_agent import discover_models
from model_review import enabled
from providers import DEFAULT_TOPIC
from report_pdf import render as render_pdf
from report import display

WEB = engine.ROOT / 'web'


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, status, data, mime='application/json; charset=utf-8', download=False, filename='research_report.md'):
        body = json.dumps(data, ensure_ascii=False).encode() if mime.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, post=False):
        origin = f'http://127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
            return self.send(403, {'error': '仅允许回环地址访问'})
        path = urlsplit(self.path).path
        try:
            data = {}
            if post:
                if self.headers.get('Origin') != origin or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    return self.send(403, {'error': '仅接受同源JSON请求'})
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 24000:
                    return self.send(413, {'error': '请求长度超出限制'})
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('请求必须为JSON对象')
            if path == '/api/health' and not post:
                return self.send(200, {'mode': 'hybrid' if enabled() else 'mock', 'api_version': 7,
                                       'features': ['feedback', 'agent_tool_choice', 'live_web_agent',
                                                    'provider_models', 'pdf_export']})
            if path == '/api/sample' and not post:
                return self.send(200, json.loads((engine.ROOT / 'data' / 'demo_manifest.json').read_text(encoding='utf-8')))
            if path == '/api/models' and post:
                return self.send(200, discover_models(data.get('provider'), data.get('api_key')))
            if path == '/api/evaluations' and not post:
                root = engine.ROOT.parent / 'evaluation_runs'
                def latest(suffix):
                    files = sorted(root.glob('*_' + suffix + '.json')) if root.exists() else []
                    return json.loads(files[-1].read_text(encoding='utf-8')) if files else None
                offline, model = latest('offline'), latest('model')
                states = [engine.load(x['run_id']) for x in engine.recent()]
                feedback = [item for path in engine.OUTPUT.glob('*/feedback.json')
                            for item in json.loads(path.read_text(encoding='utf-8'))]
                return self.send(200, {'offline': offline, 'model_probe': model.get('model_probe') if model else None,
                    'analysis': {status: sum(r.get('analysis_status') == status for r in states)
                                 for status in ('pending', 'approved', 'rejected', 'failed')},
                    'feedback': {'count': len(feedback), 'average_rating':
                                 round(sum(x['rating'] for x in feedback) / len(feedback), 2) if feedback else None}})
            if path == '/agent_workflow.svg' and not post:
                return self.send(200, (engine.ROOT.parent / 'agent_workflow.svg').read_bytes(), 'image/svg+xml; charset=utf-8')
            if path == '/api/runs':
                if not post:
                    return self.send(200, engine.recent())
                key = data.get('api_key')
                if key is not None and (not isinstance(key, str) or len(key) > 300):
                    raise ValueError('API Key格式错误')
                if key == '':
                    key = None
                r = engine.create(data.get('topic'), data.get('scenario', 'normal'),
                                  data.get('model_mode', 'mock'), key, data.get('model_name'),
                                  data.get('provider'))
                threading.Thread(target=engine.advance, args=(r['run_id'], key), daemon=True).start()
                return self.send(201, r)
            parts = path.strip('/').split('/')
            if len(parts) >= 3 and parts[:2] == ['api', 'runs']:
                run = engine.load(parts[2])
                if len(parts) == 3 and not post:
                    return self.send(200, run)
                if len(parts) == 4 and parts[3] == 'review' and post:
                    r, changed = engine.submit_review(parts[2], data.get('decisions'), api_key=data.get('api_key'))
                    if changed:
                        threading.Thread(target=engine.finish, args=(parts[2],), daemon=True).start()
                    return self.send(200, r)
                if len(parts) == 4 and parts[3] == 'analysis-review' and post:
                    return self.send(200, engine.review_analysis(parts[2], data.get('decision'), data.get('reason', '')))
                if len(parts) == 4 and parts[3] == 'analysis-retry' and post:
                    key = data.get('api_key')
                    r = engine.request_analysis_retry(parts[2], key)
                    threading.Thread(target=engine.generate_analysis, args=(parts[2], key), daemon=True).start()
                    return self.send(200, r)
                if len(parts) == 4 and parts[3] == 'live-retry' and post:
                    r = engine.request_live_retry(parts[2], data.get('api_key'), data.get('model_name'))
                    threading.Thread(target=engine.finish_live, args=(parts[2],), daemon=True).start()
                    return self.send(200, r)
                if len(parts) == 4 and parts[3] == 'feedback' and post:
                    return self.send(200, engine.save_feedback(parts[2], data.get('rating'), data.get('area'), data.get('note')))
                if len(parts) == 4 and parts[3] == 'model-report' and not post:
                    if run.get('analysis_status') != 'approved':
                        return self.send(409, {'error': '模型分析报告尚未通过人工审核'})
                    return self.send(200, (engine.folder(parts[2]) / 'model_report.md').read_bytes(),
                                     'text/markdown; charset=utf-8', True, 'model_analysis_report.md')
                if len(parts) == 4 and parts[3] == 'model-report.pdf' and not post:
                    if run.get('analysis_status') != 'approved' or run.get('mode') != 'hybrid':
                        return self.send(409, {'error': '模型分析报告尚未通过人工审核'})
                    content = (engine.folder(parts[2]) / 'model_report.md').read_text(encoding='utf-8')
                    return self.send(200, render_pdf(content), 'application/pdf', True, 'model_analysis_report.pdf')
                if len(parts) == 4 and parts[3] == 'report' and not post:
                    if run['status'] != 'COMPLETED' or not run.get('publishable', bool(run.get('approved_fact_ids'))):
                        return self.send(409, {'error': '报告尚未通过审核和检查'})
                    return self.send(200, (engine.folder(parts[2]) / 'final_report.md').read_bytes(), 'text/markdown; charset=utf-8', True)
                if len(parts) == 4 and parts[3] == 'report.pdf' and not post:
                    if run['status'] != 'COMPLETED' or not run.get('publishable', bool(run.get('approved_fact_ids'))):
                        return self.send(409, {'error': '报告尚未通过审核和检查'})
                    content = (engine.folder(parts[2]) / 'final_report.md').read_text(encoding='utf-8')
                    return self.send(200, render_pdf(content), 'application/pdf', True, 'research_report.pdf')
            if not post and path in ('/', '/app.js', '/style.css'):
                file = WEB / ('index.html' if path == '/' else path[1:])
                return self.send(200, file.read_bytes(), (mimetypes.guess_type(str(file))[0] or 'text/plain') + '; charset=utf-8')
            self.send(404, {'error': '资源不存在'})
        except FileNotFoundError:
            self.send(404, {'error': '任务或文件不存在'})
        except (ValueError, TypeError, KeyError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception as exc:
            self.send(500, {'error': '本地执行错误：' + str(exc)})

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(True)


def review_cli(run_id):
    run = engine.load(run_id)
    if run['status'] != 'WAITING_REVIEW':
        print('当前状态：', run['status'])
        return
    decisions = []
    for item in run['review_items']:
        print('\n', item['item_id'], item['entity'], item['metric'])
        for fid in item['candidate_ids']:
            f = next(f for f in run['facts'] if f['fact_id'] == fid)
            print(fid, display(f), f['source_ids'], '合成测试' if f['synthetic_test'] else f['scope'])
        selected = input('输入采用的事实ID，或 exclude 排除：').strip()
        decisions.append(dict(item_id=item['item_id'], selected_fact_id=None if selected == 'exclude' else selected,
                              reason=input('理由（冲突/排除必填）：').strip()))
    engine.submit_review(run_id, decisions, 'cli_operator')
    engine.finish(run_id)
    r = engine.load(run_id)
    print(r['status'], r.get('final_report_path', r.get('error')))


def main():
    parser = argparse.ArgumentParser(description='研迹 ResearchTrace · 离线Mock演示')
    sub = parser.add_subparsers(dest='cmd', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--open', action='store_true')
    run = sub.add_parser('run')
    run.add_argument('--topic', default=DEFAULT_TOPIC)
    run.add_argument('--scenario', choices=['normal', 'exception'], default='normal')
    resume = sub.add_parser('resume')
    resume.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error('需要Python 3.11或更高版本')
    if args.cmd == 'serve':
        engine.recover_interrupted()
        try:
            server = LocalServer(('127.0.0.1', args.port), Handler)
        except OSError:
            parser.error(f'本地端口 {args.port} 已占用。请关闭旧的研究工作台启动窗口后重试。')
        url = f'http://127.0.0.1:{server.server_port}'
        print('ResearchTrace / ' + ('Model-assisted review' if enabled() else 'Offline Mock') + ' / ' + url, flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()
    elif args.cmd == 'run':
        r = engine.create(args.topic, args.scenario)
        engine.advance(r['run_id'])
        print('run_id:', r['run_id'])
        review_cli(r['run_id'])
    else:
        review_cli(args.run_id)


if __name__ == '__main__':
    main()
