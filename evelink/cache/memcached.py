import pickle
import time
import memcache

from evelink import api

class MemcachedCache(api.APICache):
    """An implementation of APICache using memcached."""

    def __init__(self, serverList, prefix='el_'):
        """
        serverList could be ['127.0.0.1:11211']
        prefix allows multiple applications to share the same memcached instance if necessary
        """
        super(MemcachedCache, self).__init__()
        self.mc = memcache.Client(serverList, debug=0)
        self.prefix = prefix


    def get(self, key):
        prefix_key = self.prefix + key
        result = self.mc.get(prefix_key)
        if not result:
            return None
        value, expiration = result
        if expiration < time.time():
            self.mc.delete(prefix_key)
            return None
        return value

    def put(self, key, value, duration):
        prefix_key = self.prefix + key
        expiration = time.time() + duration
        res = self.mc.set(prefix_key, (value, expiration), time=duration)
        return res

