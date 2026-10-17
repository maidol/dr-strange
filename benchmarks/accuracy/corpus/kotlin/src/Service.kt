package acme

/** An unrelated class whose `get` shares a name with Store.get. */
class Cache {
    private val hits = mutableMapOf<Int, String>()

    fun get(key: Int): String? = hits[key]
}

/** Constructor injection: the property's type is the interface. */
class Service(private val store: Store) {

    /** Through the interface — the implementation is a runtime fact. */
    fun load(key: String): String? = store.get(key)

    /** Through the interface, plus a top-level function. */
    fun save(key: String, value: String) {
        store.put(key, normalize(value))
    }

    /** The parameter is typed Cache, so the target is written down. */
    fun warm(cache: Cache, key: Int): String? = cache.get(key)

    /** A call on `this` within the same class. */
    fun describe(): String = label()

    private fun label(): String = "service"
}
