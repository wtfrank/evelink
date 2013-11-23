import functools
import time
from urllib import urlencode
from xml.etree import ElementTree

from google.appengine.api import memcache
from google.appengine.api import urlfetch
from google.appengine.ext import ndb

from evelink import api



class AppEngineAPI(api.API):
    """Subclass of api.API that is compatible with Google Appengine."""

    def __init__(self, base_url="api.eveonline.com", cache=None, api_key=None):
        cache = cache or AppEngineCache()
        super(AppEngineAPI, self).__init__(base_url=base_url,
                cache=cache, api_key=api_key)

    @ndb.tasklet
    def get_async(self, path, params):
        """Asynchronous request a specific path from the EVE API.
        
        TODO: refactor evelink.api.API.get

        """
        params = params or {}
        params = dict((k, api._clean(v)) for k,v in params.iteritems())

        if self.api_key:
            params['keyID'] = self.api_key[0]
            params['vCode'] = self.api_key[1]

        key = self._cache_key(path, params)
        response = yield self.cache.get_async(key)
        cached = response is not None

        if not cached:
            # no cached response body found, call the API for one.
            params = urlencode(params)
            full_path = "https://%s/%s.xml.aspx" % (self.base_url, path)
            response = yield self.send_request_async(full_path, params)

        tree = ElementTree.fromstring(response)
        current_time = api.get_ts_value(tree, 'currentTime')
        expires_time = api.get_ts_value(tree, 'cachedUntil')
        self._set_last_timestamps(current_time, expires_time)

        if not cached:
            yield self.cache.put_async(key, response, expires_time - current_time)

        error = tree.find('error')
        if error is not None:
            code = error.attrib['code']
            message = error.text.strip()
            exc = api.APIError(code, message, current_time, expires_time)
            raise exc

        result = tree.find('result')
        raise ndb.Return(api.APIResult(result, current_time, expires_time))

    def send_request(self, url, params):
        """Send a request via the urlfetch API.

        url:
            The url to fetch
        params:
            URL encoded parameters to send. If set, will use a form POST,
            otherwise a GET.
        """
        return self.send_request_async(url, params).get_result()

    @ndb.tasklet
    def send_request_async(self, url, params):
        ctx = ndb.get_context()
        result = yield ctx.urlfetch(
            url=url,
            payload=params,
            method=urlfetch.POST if params else urlfetch.GET,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
                    if params else {}
        )
        raise ndb.Return(result.content)


class AppEngineCache(api.APICache):
    """Memcache backed APICache implementation."""
    
    def get(self, key):
        return memcache.get(key)

    @ndb.tasklet
    def get_async(self, key):
        """Dummy async method.

        Memcache doesn't have an async get method.

        """
        raise ndb.Return(self.get(key))


    def put(self, key, value, duration):
        if duration < 0:
            duration = time.time() + duration
        memcache.set(key, value, time=duration)

    @ndb.tasklet
    def put_async(self, key,  value, duration):
        """Dummy async method (see get_async).

        """
        self.put(key, value, duration)


class EveLinkCache(ndb.Model):
    value = ndb.PickleProperty()
    expiration = ndb.IntegerProperty()


class AppEngineDatastoreCache(api.APICache):
    """An implementation of APICache using the AppEngine datastore."""

    def __init__(self):
        super(AppEngineDatastoreCache, self).__init__()

    def get(self, cache_key):
        return self.get_async(cache_key).get_result()

    @ndb.tasklet
    def get_async(self, cache_key):
        db_key = ndb.Key(EveLinkCache, cache_key)
        result = yield db_key.get_async()

        if not result:
            raise ndb.Return(None)
        
        if result.expiration < time.time():
            yield db_key.delete_async()
            raise ndb.Return(None)
        
        raise ndb.Return(result.value)

    def put(self, cache_key, value, duration):
        self.put_async(cache_key, value, duration).get_result()

    @ndb.tasklet
    def put_async(self, cache_key, value, duration):
        expiration = int(time.time() + duration)
        cache = EveLinkCache(id=cache_key, value=value, expiration=expiration)
        yield cache.put_async()


def auto_gae_api(func):
    """A decorator to automatically provide an API instance.

    Functions decorated with this will have the api= kwarg
    automatically supplied with a default-initialized API()
    object if no other API object is supplied.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if 'api' not in kwargs:
            kwargs['api'] = AppEngineAPI()
        return func(*args, **kwargs)
    return wrapper

def _make_async(method_name, method):
    def _async(self):
        api_result = yield self.api.get_async(method._path)
        raise ndb.Return(getattr(self, method_name)(api_result=api_result))
    return ndb.tasklet(_async)

def auto_async(cls):
    """Class decoration which add a async version of any method with a
    a '_path' attribute, which the auto_call decorator adds.

    """

    for attr_name in dir(cls):

        attr = getattr(cls, attr_name)
        if not hasattr(attr, '_path'):
            continue
        
        async_method = _make_async(attr_name, attr)
        async_method.__doc__ = """Asynchronous version of %s.""" % attr_name
        setattr(cls, '%s_async' % attr_name, async_method)
        
    return cls
