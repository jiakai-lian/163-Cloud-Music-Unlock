import sys
import socket
import base64
import tornado.httpserver
import tornado.ioloop
import tornado.iostream
import tornado.web
import tornado.httpclient
import hashlib
import json
import re
import logging
import traceback
from optparse import OptionParser


HOST_MODE = True

logger = logging.getLogger('')
__all__ = ['ProxyHandler', 'run_proxy']
counter = 0

class StatHandler(tornado.web.RequestHandler):

    def get(self):
        self.write('Until startup process:%s' % counter)
        self.finish()

class ProxyHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ['GET', 'POST', 'CONNECT']

    re_url = re.compile('/eapi/(v3/song/detail/|v1/album/|v3/playlist/detail|batch|cloudsearch/pc|v1/artist|v1/search/get)')

    @tornado.web.asynchronous
    @tornado.gen.coroutine
    def get(self):

        global counter
        counter += 1

        def encrypted_id(id):
            magic = bytearray('3go8&$8*3*3h0k(2)2')
            song_id = bytearray(str(id))
            magic_len = len(magic)
            for i in xrange(len(song_id)):
                song_id[i] = song_id[i] ^ magic[i % magic_len]
            m = hashlib.md5(song_id)
            result = m.digest().encode('base64')[:-1]
            result = result.replace('/', '_')
            result = result.replace('+', '-')
            return result

        def gen_mp3url(dfsId):
            e = encrypted_id(dfsId)
            return 'http://m2.music.126.net/%s/%s.mp3' % (e, dfsId)

        def choose_br(song, USE_H_BITRATE=False):
            if 'mp3Url' in song and not USE_H_BITRATE:
                mp3url = song['mp3Url']
                if 'lMusic' in song:
                    br = song['lMusic']['bitrate']
                else:
                    br = 96000
            else:
                if 'hMusic' in song:
                    dfsId = song['hMusic']['dfsId']
                    br = song['hMusic']['bitrate']
                elif 'mMusic' in song:
                    dfsId = song['mMusic']['dfsId']
                    br = song['mMusic']['bitrate']
                elif 'lMusic' in song:
                    dfsId = song['lMusic']['dfsId']
                    br = song['lMusic']['bitrate']
                elif 'bMusic' in song:
                    dfsId = song['bMusic']['dfsId']
                    br = song['bMusic']['bitrate']
                mp3url = gen_mp3url(dfsId)
            return br, mp3url

        def eapi_batch(response_body):
            rereobj = re.compile('"st":-?\d+')
            response_body, _ = rereobj.subn('"st":0', response_body)
            rereobj = re.compile('"subp":\d+')
            response_body, _ = rereobj.subn('"subp":1', response_body)
            rereobj = re.compile('"dl":0')
            response_body, _ = rereobj.subn('"dl":320000', response_body)
            rereobj = re.compile('"pl":0')
            response_body, _ = rereobj.subn('"pl":320000', response_body)
            return response_body

        def eapi_song_download_limit():
            return "{\"overflow\":false,\"code\":200}"

        url = self.request.uri
        if HOST_MODE:
            url = 'http://music.163.com' + url
            
        req = tornado.httpclient.HTTPRequest(url=url,
            method=self.request.method, body=self.request.body,
            headers=self.request.headers, follow_redirects=False,
            allow_nonstandard_methods=True)

        client = tornado.httpclient.AsyncHTTPClient()
        try:
            response = yield client.fetch(req)
            print self.request.uri
            response_body = response.body
            if ProxyHandler.re_url.search(self.request.uri):
                response_body = eapi_batch(response_body)
            elif '/eapi/song/enhance/download/url' in  self.request.uri:
                j = json.loads(response_body)
                if 'br' in j['data'] and j['data']['br'] <= 128000:
                    try:
                        sid = j['data']['id']
                        client = tornado.httpclient.AsyncHTTPClient()
                        url = 'http://music.163.com/api/song/detail?id=%s&ids=[%s]' % (sid, sid)
                        response_ = yield client.fetch(url)
                        j = json.loads(response_.body)
                        br,  mp3url = choose_br(j['songs'][0], USE_H_BITRATE=True)
                        client = tornado.httpclient.AsyncHTTPClient()
                        response_ = yield client.fetch(mp3url, method='HEAD')
                        size = int(response_.headers['Content-Length'])
                        j = json.loads(response_body)
                        j['data']['br'] = br
                        j['data']['url'] = mp3url
                        del j['data']['md5']
                        j['data']['size'] = size
                        response_body = json.dumps(j)
                    except:
                        traceback.print_exc()
            elif '/eapi/song/enhance/player/url' in self.request.uri and '"url":null' in response_body:
                # get mp3 url
                re_id = re.compile('"id":(\d+)')
                sid = re_id.search(response_body).group(1)
                client = tornado.httpclient.AsyncHTTPClient()
                url = 'http://music.163.com/api/song/detail?id=%s&ids=[%s]' % (sid, sid)
                response_ = yield client.fetch(url)
                j = json.loads(response_.body)
                br,  mp3url = choose_br(j['songs'][0], USE_H_BITRATE=False)
                j = json.loads(response_body)
                j['data'][0]['url'] = mp3url
                j['data'][0]['br'] = br
                j['data'][0]['code'] = 200
                #del j['data']['md5']
                response_body = json.dumps(j)

            elif '/eapi/song/download/limit' in response.body:
                response_body = eapi_song_download_limit()
            #print response_body[:5000]

            if response.error and not isinstance(response.error,
                    tornado.httpclient.HTTPError):
                self.set_status(500)
                self.write('Internal server error:\n' + str(response.error))
                self.finish()
            else:
                self.set_status(response.code)
                for header in ('Date', 'Cache-Control', 'Server',
                        'Content-Type', 'Location'):
                    v = response.headers.get(header)
                    if v:
                        self.set_header(header, v)
                if response.body:
                    self.write(response_body)
                self.finish()
        except tornado.httpclient.HTTPError as e:
            if hasattr(e, 'response') and e.response:
                #self.handle_response(e.response)
                if 300 <= e.response.code <= 399:
                    self.set_status(e.response.code)
                    if e.response.body:
                        self.write(e.response.body)
                    self.finish()
                else:
                    traceback.print_exc()
            else:
                self.set_status(500)
                self.write('Internal server error:\n' + str(e))
                self.finish()

    @tornado.web.asynchronous
    def post(self):
        return self.get()

    @tornado.web.asynchronous
    def connect(self):
        host, port = self.request.uri.split(':')
        client = self.request.connection.stream

        def read_from_client(data):
            upstream.write(data)

        def read_from_upstream(data):
            client.write(data)

        def client_close(data=None):
            if upstream.closed():
                return
            if data:
                upstream.write(data)
            upstream.close()

        def upstream_close(data=None):
            if client.closed():
                return
            if data:
                client.write(data)
            client.close()

        def start_tunnel():
            client.read_until_close(client_close, read_from_client)
            upstream.read_until_close(upstream_close, read_from_upstream)
            client.write(b'HTTP/1.0 200 Connection established\r\n\r\n')

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        upstream = tornado.iostream.IOStream(s)
        upstream.connect((host, int(port)), start_tunnel)


def run_proxy(eth, start_ioloop=True):
    """
    Run proxy on the specified port. If start_ioloop is True (default),
    the tornado IOLoop will be started immediately.
    """
    app = tornado.web.Application([
        ('/stat', StatHandler),
        (r'\S+', ProxyHandler),
    ])
    app.listen(port=eth[1], address=eth[0])
    ioloop = tornado.ioloop.IOLoop.instance()
    if start_ioloop:
        ioloop.start()

if __name__ == '__main__':
    parser = OptionParser(usage=u'usage: %prog [options]')
    parser.add_option('-p', '--port', dest='port', action='store', type='int', default=16163,
                      help='Listening port')
    parser.add_option('-a', '--addr', dest='addr', action='store',
                      metavar='addr',default='127.0.0.1',
                      help='Bind address')
    parser.add_option('-m', '--mode', dest='mode', action='store', type='string', default='proxy',
                  help='Work mode [server] or [proxy]')
    (options,args) = parser.parse_args()

    if options.mode == 'proxy':
        HOST_MODE = False
    elif options.mode == 'server':
        HOST_MODE = True
    else:
        logger.error('error mode')
        exit(1)
    eth = (options.addr, options.port)
    print ("Starting HTTP %s on port %s" % (options.mode, str(eth)))
    run_proxy(eth)
