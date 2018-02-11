#!/usr/bin/env python3

import json
from requests import Session, adapters, exceptions
from requests.compat import urlparse, urlunparse, urlencode
import asyncio
import aiohttp
import async_timeout
from requests.compat import urlparse, urlunparse, urlencode
import websockets
import ssl

from embypy import __version__

adapters.DEFAULT_RETRIES = 5

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
    self.url        = url
    self.conn       = conn
    if type(ssl_str) == str:
      self.ssl = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
      self.ssl.load_verify_locations(cafile=ssl_str)
    else:
      self.ssl = None

  def connect(self):
    '''Establish a connection'''
    #TODO - authenticate to emby
    asyncio.get_event_loop().create_task(self.handler())

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

  async def send(message):
    if not self.ws:
      return False
    return await self.ws.send(message)

  def send_sync(message):
    return asyncio.get_event_loop().run_until_complete(self.send(message))

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
  api-key : str
    api key generated by emby, used for authentication
  address-remote : str, optional
    alt url to connect to, pulic facing (see notes)
  ssl : str, optional
    path to ssl certificate - for self signed certs
  userid : str, optional
    emby id of the user you wish to connect as
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
  loop : asyncio.AbstractEventLoop
    if given all calls should be awaitable

  Notes
  -----
  This class/object should NOT be used (except internally).

  Tf a address-remote url is given, then that will be used for output,
  such as the `embypy.objects.EmbyObject.url` atribute.

  `url` will always be used when making requests - thus I recomend using
  the local address for `url` and the remote address
  for `address-remote`

  username/password authentication is not supported as of yet
  '''
  def __init__(self, url, **kargs):
    if ('api_key'  not in kargs or 'device_id' not in kargs) and \
       ('username' not in kargs or 'password'  not in kargs):
      raise ValueError('provide api key and device id or username/password')

    urlremote      = kargs.get('address-remote')
    self.ssl       = kargs.get('ssl', True)
    self.userid    = kargs.get('userid')
    self.api_key   = kargs.get('api_key')
    self.username  = kargs.get('username')
    self.password  = kargs.get('password')
    self.device_id = kargs.get('device_id')
    self.timeout   = kargs.get('timeout', (6.1, 27))
    self.tries     = kargs.get('tries', 3)
    self.loop      = kargs.get('loop', asyncio.get_event_loop())
    self.url       = urlparse(url)
    self.urlremote = urlparse(urlremote) if urlremote else urlremote
    self.token     = ''
    self.session   = aiohttp.ClientSession()

    #connect to websocket is user wants to
    if 'ws' in kargs:
      self.ws = WebSocket(self, self.get_url(websocket=True), self.ssl)
    else:
      self.ws = None

    self.session.headers.update(
      {'Authorization':
       'MediaBrowser Client="{0}", Device="{0}", DeviceId="{1}", Version="{2}"'
        .format('Navi', self.device_id, __version__)
      }
    )

    # authenticate to emby if password was given
    if self.password and self.username:
      data = self.post('/Users/AuthenticateByName',
                                 data=pw(self.password),
                                 format='json',
                                 username=self.username
      ).json()
      self.token = data['AccessToken']
      self.session.headers.update(
             {'X-MediaBrowser-Token', self.token}
      )

  def __del__(self):
    try:
      asyncio.get_event_loop().run_until_complete(self.session.close())
    except:
      pass

  @staticmethod
  def sync_run(self, f):
    if asyncio.iscoroutinefunction(f):
      f = f()

    if asyncio.iscoroutine(f):
      return asyncio.get_event_loop().run_until_complete(f)
    elif callable(f):
      return f()
    else:
      return f

  def get_sync(self, *args, **kargs):
    return self.sync_run(self.get(*args, **kargs))

  def delete_sync(self, *args, **kargs):
    return self.sync_run(self.delete(*args, **kargs))

  def post_sync(self, *args, **kargs):
    return self.sync_run(self.post(*args, **kargs))

  def getJson_sync(self, *args, **kargs):
    return self.sync_run(self.getJson(*args, **kargs))

  def get_url(self, path='/', websocket=False, remote=True,
              attach_api_key=True, userId=None, pass_uid=False, **query):
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
    if attach_api_key:
      query.update({'api_key':self.api_key, 'deviceId': self.device_id})
    if pass_uid:
      query['userId'] = userId

    if remote:
      url = self.urlremote or self.url
    else:
      url = self.url

    if websocket:
      scheme = {'http':'ws', 'https':'wss'}[url.scheme]
    else:
      scheme = url.scheme
    netloc = url.netloc + '/emby'

    url = urlunparse((scheme, netloc, path, '', '{params}', '')).format(
      UserId   = userId,
      ApiKey   = self.api_key,
      DeviceId = self.device_id,
      params   = urlencode(query)
    )

    return url[:-1] if url[-1] == '?' else url

  def add_on_message(self, func):
    '''add function that handles websocket messages'''
    return self.ws.on_message.append(func)

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
    url = self.get_url(path, **query)

    for i in range(self.tries):
      try:
        return await self.session.get(url,
                                      timeout=self.timeout,
                                      verify=self.ssl
        )
      except exceptions.Timeout:
        if i>= self.tries-1:
          raise exceptions.Timeout('Timeout ', url)
      except exceptions.ConnectionError:
        if i>= self.tries-1:
          raise exceptions.ConnectionError('Emby server is probably down')

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
    url = self.get_url(path, **query)

    for i in range(self.tries):
      try:
        return await self.session.delete(url,
                                         timeout=self.timeout,
                                         verify=self.ssl
        )
      except exceptions.Timeout:
        if i>= self.tries-1:
          raise exceptions.Timeout('Timeout ', url)
      except exceptions.ConnectionError:
        if i>= self.tries-1:
          raise exceptions.ConnectionError('Emby server is probably down')

  async def post(self, path, data={}, **params):
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
    url = self.get_url(path, **params)
    for i in range(self.tries):
      try:
        return await self.session.post(url,
                                       json=data,
                                       timeout=self.timeout,
                                       verify=self.ssl
        )
      except exceptions.Timeout:
        if i>= self.tries-1:
          raise exceptions.Timeout('Timeout ', url)
      except exceptions.ConnectionError:
        if i>= self.tries-1:
          raise exceptions.ConnectionError('Emby server is probably down')


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
    return await (await self.get(path, **query)).json()
