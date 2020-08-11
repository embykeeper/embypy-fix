import json
from requests.compat import urlparse, urlunparse, urlencode
import asyncio
import aiohttp
import websockets
import ssl

from embypy import __version__
from embypy.utils.asyncio import async_func


class WebSocket:
    '''Basic websocet that runs function when messages are recived

    Parameters
    ----------
    conn : embypy.utils.Connector
      connector object
    url : str
      uri of websocet server
    ssl_str : str
      path to the ssl certificate for confirmation
    '''
    def __init__(self, conn, url, ssl_str=None):
        self.on_message = []
        self.url	= url
        self.conn	= conn
        if type(ssl_str) == str:
            self.ssl = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
            self.ssl.load_verify_locations(cafile=ssl_str)
        else:
            self.ssl = ssl_str

    def __setattr__(self, name, value):
        if name.endswith('_sync'):
            return self.__setattr__(name[:-5], value)
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name.endswith('_sync'):
            return self.__getattr__(name[:-5])
        return self.__getattribute__(name)

    def connect(self):
        '''Establish a connection'''
        # TODO - authenticate to emby
        #self.loop.create_task(self.handler())

    @async_func
    async def handler(self):
        '''Handle loop, get and process messages'''
        self.ws = await websockets.connect(self.url, ssl=self.ssl)
        while self.ws:
            message = await self.ws.recv()
            for handle in self.on_message:
                if asyncio.iscoroutinefunction(handle):
                    await handle(self, message)
                else:
                    handle(self, message)

    @async_func
    async def send(self, message):
        if not self.ws:
            return False
        return await self.ws.send(message)

    def close(self):
        '''close connection to socket'''
        self.ws.close()
        self.ws = None


class Connector:
    '''Class responsible for comunication with emby

    Parameters
    ----------
    url : str
      url to connect to
    address-remote : str, optional
      alt url to connect to, pulic facing (see notes)
    ssl : str, optional
      path to ssl certificate - for self signed certs
    userid : str, optional
      emby id of the user you wish to connect as
    api-key : str
      api key generated by emby, used for authentication
    token : str
      similar to api key, but is meant for user logins
    username : str, optional
      username for login (see notes)
    password : str, optional
      password for login (see notes)
    device_id : str
      device id as registered in emby
    timeout : int
      number of seconds to wait before timeout for a request
    tries : int
      number of times to try a request before throwing an error
    jellyfin : bool
      if this is a jellyfin (false = emby) server

    Notes
    -----
    This class/object should NOT be used (except internally).

    Tf a address-remote url is given, then that will be used for output,
    such as the `embypy.objects.EmbyObject.url` atribute.

    `url` will always be used when making requests - thus I recomend using
    the local address for `url` and the remote address
    for `address-remote`

    Jellyfin and emby have some url differences right now,
    so set jellyfin's url scheme to true/false
    [or None (default) for auto-detect]
    '''
    def __init__(self, url, **kargs):
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        if ('api_key'  not in kargs or 'userid'   not in kargs) and \
           ('username' not in kargs or 'password' not in kargs):
            raise ValueError(
                'provide api key and userid or username/password'
            )

        urlremote	= kargs.get('address-remote')
        self.ssl	= kargs.get('ssl', True)
        self.userid	= kargs.get('userid')
        self.token	= kargs.get('token')
        self.api_key	= kargs.get('api_key', self.token)
        self.username	= kargs.get('username')
        self.password	= kargs.get('password')
        self.device_id	= kargs.get('device_id', 'EmbyPy')
        self.timeout	= kargs.get('timeout', 30)
        self.tries	= kargs.get('tries', 3)
        self.jellyfin	= kargs.get('jellyfin')
        self.url	= urlparse(url)
        self.urlremote	= urlparse(urlremote) if urlremote else urlremote

        self.attempt_login = False
        self._session_locks = {}
        self._session_uses = {}
        self._sessions = {}

        if self.ssl and type(self.ssl) == str:
            self.ssl = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
            self.ssl.load_verify_locations(cafile=self.ssl)

        # connect to websocket is user wants to
        if 'ws' in kargs:
            self.ws = WebSocket(self, self.get_url(websocket=True), self.ssl)
        else:
            self.ws = None

    def __setattr__(self, name, value):
        if name.endswith('_sync'):
            return self.__setattr__(name[:-5], value)
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name.endswith('_sync'):
            return self.__getattr__(name[:-5])
        return self.__getattribute__(name)

    async def _get_session(self):
        loop = asyncio.get_running_loop()
        loop_id = hash(loop)
        headers = {
            'Authorization': (
                'MediaBrowser Client="{0}",Device="{0}",'
                'DeviceId="{1}",'
                'Version="{2}"'
            ).format('EmbyPy', self.device_id, __version__),
        }

        if self.token:
            headers.update({'X-MediaBrowser-Token': self.token})

        async with await self._get_session_lock():
            session = self._sessions.get(loop_id)
            if not session:
                session = aiohttp.ClientSession(
                    headers=headers,
                    connector=aiohttp.TCPConnector(ssl_context=self.ssl),
                )
                self._sessions[loop_id] = session
                self._session_uses[loop_id] = 1
            else:
                self._session_uses[loop_id] += 1
            return session

    async def _end_session(self):
        loop = asyncio.get_running_loop()
        loop_id = hash(loop)
        async with await self._get_session_lock():
            self._session_uses[loop_id] -= 1
            session = self._sessions.get(loop_id)
            if session and self._session_uses[loop_id] <= 0:
                await session.close()
                self._sessions[loop_id] = None

    async def _get_session_lock(self):
        loop = asyncio.get_running_loop()
        self._sessions[loop] = None
        return self._session_locks.setdefault(loop, asyncio.Lock(loop=loop))

    @async_func
    async def info(self):
        return await self.getJson(
            '/system/info/public',
            remote=False,
        )

    @property
    @async_func
    async def is_jellyfin(self):
        if self.jellyfin is None:
            info = await self.info()
            product = info.get('ProductName', '')
            self.jellyfin = 'jellyfin' in product.lower()
        return self.jellyfin

    @async_func
    async def login_if_needed(self):
        # authenticate to emby if password was given
        if self.password and self.username and not self.token:
            return await self.login()

    @async_func
    async def login(self):
        if not self.username or self.attempt_login:
            return

        self.attempt_login = True
        try:
            data = await self.postJson(
                '/Users/AuthenticateByName',
                data={
                    'username': self.username,
                    'pw': self.password,
                },
                send_raw=True,
                format='json',
            )

            self.token = data.get('AccessToken', '')
            self.userid = data.get('User', {}).get('Id')
            self.api_key = self.token

            session = await self._get_session()
            session._default_headers['X-MediaBrowser-Token'] = self.token
            await self._end_session()
        finally:
            self.attempt_login = False

    def get_url(
        self, path='/', websocket=False, remote=True,
        attach_api_key=True, userId=None, pass_uid=False, **query
    ):
        '''construct a url for an emby request

        Parameters
        ----------
        path : str
          uri path(excluding domain and port) of get request for emby
        websocket : bool, optional
          if true, then `ws(s)` are used instead of `http(s)`
        remote : bool, optional
          if true, remote-address is used (default True)
        attach_api_key : bool, optional
          if true, apikey is added to the query (default True)
        userId : str, optional
          uid to use, if none, default is used
        pass_uid : bool, optional
          if true, uid is added to the query (default False)
        query : karg dict
          additional parameters to set (part of url after the `?`)

        Also See
        --------
          get :
          getJson :
          post :
          delete :

        Returns
        -------
        full url
        '''
        userId = userId or self.userid
        if attach_api_key and self.api_key:
            query.update({
                'api_key': self.api_key,
                'deviceId': self.device_id
            })
        if pass_uid:
            query['userId'] = userId

        if remote:
            url = self.urlremote or self.url
        else:
            url = self.url

        if websocket:
            scheme = url.scheme.replace('http', 'ws')
        else:
            scheme = url.scheme

        url = urlunparse(
            (scheme, url.netloc, path, '', '{params}', '')
        ).format(
            UserId	= userId,
            ApiKey	= self.api_key,
            DeviceId	= self.device_id,
            params	= urlencode(query)
        )

        return url[:-1] if url[-1] == '?' else url

    @async_func
    async def _process_resp(self, resp):
        if (not resp or resp.status == 401) and self.username:
            await self.login()
            return False
        return True

    @staticmethod
    @async_func
    async def resp_to_json(resp):
        try:
            return await resp.json()
        except aiohttp.client_exceptions.ContentTypeError:
            raise RuntimeError(
                'Unexpected JSON output (status: {}): "{}"'.format(
                    resp.status,
                    await resp.text(),
                )
            )

    def add_on_message(self, func):
        '''add function that handles websocket messages'''
        return self.ws.on_message.append(func)

    @async_func
    async def _req(self, method, path, params={}, **query):
        await self.login_if_needed()
        for i in range(self.tries):
            url = self.get_url(path, **query)
            try:
                resp = await method(url, timeout=self.timeout, **params)
                if await self._process_resp(resp):
                    return resp
                else:
                    continue
            except aiohttp.ClientConnectionError:
                pass
        raise aiohttp.ClientConnectionError(
            'Emby server is probably down'
        )

    @async_func
    async def get(self, path, **query):
        '''return a get request

        Parameters
        ----------
        path : str
          same as get_url
        query : kargs dict
          additional info to pass to get_url

        See Also
        --------
          get_url :
          getJson :

        Returns
        -------
        requests.models.Response
          the response that was given
        '''
        try:
            session = await self._get_session()
            async with await self._req(
                session.get,
                path,
                **query
            ) as resp:
                return resp.status, await resp.text()
        finally:
            await self._end_session()

    @async_func
    async def delete(self, path, **query):
        '''send a delete request

        Parameters
        ----------
        path : str
          same as get_url
        query : kargs dict
          additional info to pass to get_url

        See Also
        --------
        get_url :

        Returns
        -------
        requests.models.Response
          the response that was given
        '''
        try:
            session = await self._get_session()
            async with await self._req(
                session.delete,
                path,
                **query
            ) as resp:
                return resp.status
        finally:
            await self._end_session()

    @async_func
    async def post(self, path, data={}, send_raw=False, **query):
        '''sends post request

        Parameters
        ----------
        path : str
          same as get_url
        data : dict
          post data to send
        send_raw : bool
          if true send data as post data, otherwise send as a json string
        query : kargs dict
          additional info to pass to get_url

        See Also
        --------
          postJson :
          get_url :

        Returns
        -------
        requests.models.Response
          the response that was given
        '''
        return await self._post(
            path,
            return_json=False,
            data=data,
            send_raw=send_raw,
            **query,
        )

    @async_func
    async def postJson(self, path, data={}, send_raw=False, **query):
        '''sends post request

        Parameters
        ----------
        path : str
          same as get_url
        data : dict
          post data to send
        send_raw : bool
          if true send data as post data, otherwise send as a json string
        query : kargs dict
          additional info to pass to get_url

        See Also
        --------
          post :
          get_url :

        Returns
        -------
        requests.models.Response
          the response that was given
        '''
        return await self._post(
            path,
            return_json=True,
            data=data,
            send_raw=send_raw,
            **query,
        )

    @async_func
    async def _post(self, path, return_json, data, send_raw, **query):
        '''sends post request

        Parameters
        ----------
        path : str
          same as get_url
        query : kargs dict
          additional info to pass to get_url

        See Also
        --------
          get_url :

        Returns
        -------
        requests.models.Response
          the response that was given
        '''
        jstr = json.dumps(data)
        try:
            session = await self._get_session()
            if send_raw:
                params = {"data": data}
            else:
                params = {"data": jstr}
            async with await self._req(
                session.post,
                path,
                params=params,
                **query
            ) as resp:
                if return_json:
                    return await Connector.resp_to_json(resp)
                else:
                    return resp.status, await resp.text()
        finally:
            await self._end_session()

    @async_func
    async def getJson(self, path, **query):
        '''wrapper for get, parses response as json

        Parameters
        ----------
        path : str
          same as get_url
        query : kargs dict
          additional info to pass to get_url

        See Also
        --------
          get_url :
          get :

        Returns
        -------
        dict
          the response content as a dict
        '''
        try:
            session = await self._get_session()
            async with await self._req(
                session.get,
                path,
                **query
            ) as resp:
                return await Connector.resp_to_json(resp)
        finally:
            await self._end_session()
