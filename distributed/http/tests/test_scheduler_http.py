import json
import sys

from tornado.ioloop import IOLoop
from tornado import web, gen
from tornado.httpclient import AsyncHTTPClient
from tornado.httpserver import HTTPServer

from distributed import Scheduler, Executor
from distributed.executor import _wait
from distributed.utils_test import gen_cluster, gen_test, inc
from distributed.http.scheduler import HTTPScheduler
from distributed.http.worker import HTTPWorker


@gen_cluster()
def test_simple(s, a, b):
    server = HTTPScheduler(s)
    server.listen(0)
    client = AsyncHTTPClient()

    response = yield client.fetch('http://localhost:%d/info.json' % server.port)
    response = json.loads(response.body.decode())
    assert response['ncores'] == {'%s:%d' % k: v for k, v in s.ncores.items()}
    assert response['status'] == a.status

    server.stop()


@gen_cluster()
def test_processing(s, a, b):
    server = HTTPScheduler(s)
    server.listen(0)
    client = AsyncHTTPClient()

    s.processing[a.address].add(('foo-1', 1))

    response = yield client.fetch('http://localhost:%d/processing.json' % server.port)
    response = json.loads(response.body.decode())
    assert response == {a.address_string: ['foo'], b.address_string: []}

    server.stop()


@gen_cluster()
def test_proxy(s, a, b):
    server = HTTPScheduler(s)
    server.listen(0)
    worker = HTTPWorker(a)
    worker.listen(0)
    client = AsyncHTTPClient()

    c_response = yield client.fetch('http://localhost:%d/info.json' % worker.port)
    s_response = yield client.fetch('http://localhost:%d/proxy/%s:%d/info.json'
                                    % (server.port, a.ip, worker.port))
    assert s_response.body.decode() == c_response.body.decode()
    server.stop()
    worker.stop()


@gen_cluster()
def test_broadcast(s, a, b):
    ss = HTTPScheduler(s)
    ss.listen(0)
    s.services['http'] = ss

    aa = HTTPWorker(a)
    aa.listen(0)
    a.services['http'] = aa
    a.service_ports['http'] = aa.port
    s.worker_services[a.address]['http'] = aa.port

    bb = HTTPWorker(b)
    bb.listen(0)
    b.services['http'] = bb
    b.service_ports['http'] = bb.port
    s.worker_services[b.address]['http'] = bb.port

    client = AsyncHTTPClient()

    a_response = yield client.fetch('http://localhost:%d/info.json' % aa.port)
    b_response = yield client.fetch('http://localhost:%d/info.json' % bb.port)
    s_response = yield client.fetch('http://localhost:%d/broadcast/info.json'
                                    % ss.port)
    assert (json.loads(s_response.body.decode()) ==
            {a.address_string: json.loads(a_response.body.decode()),
             b.address_string: json.loads(b_response.body.decode())})

    ss.stop()
    aa.stop()
    bb.stop()


@gen_test()
def test_services():
    s = Scheduler(services={'http': HTTPScheduler})
    assert isinstance(s.services['http'], HTTPServer)
    assert s.services['http'].port


@gen_cluster()
def test_with_data(s, a, b):
    ss = HTTPScheduler(s)
    ss.listen(0)

    e = Executor((s.ip, s.port), start=False)
    yield e._start()

    L = e.map(inc, [1, 2, 3])
    L2 = yield e._scatter(['Hello', 'world!'])
    yield _wait(L)

    client = AsyncHTTPClient()
    response = yield client.fetch('http://localhost:%s/memory-load.json' %
                                  ss.port)
    out = json.loads(response.body.decode())

    assert all(isinstance(v, int) for v in out.values())
    assert set(out) == {a.address_string, b.address_string}
    assert sum(out.values()) == sum(map(sys.getsizeof,
                                        [1, 2, 3, 'Hello', 'world!']))

    response = yield client.fetch('http://localhost:%s/memory-load-by-key.json'
                                  % ss.port)
    out = json.loads(response.body.decode())
    assert set(out) == {a.address_string, b.address_string}
    assert all(isinstance(v, dict) for v in out.values())
    assert all(k in {'inc', 'data'} for d in out.values() for k in d)
    assert all(isinstance(v, int) for d in out.values() for v in d.values())

    assert sum(v for d in out.values() for v in d.values()) == \
            sum(map(sys.getsizeof, [1, 2, 3, 'Hello', 'world!']))

    ss.stop()
    yield e._shutdown()
