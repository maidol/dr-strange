package acme

/** The interface a service depends on. */
interface Store {
    fun get(key: String): String?
    fun put(key: String, value: String)
}

/** One implementation. */
class MemStore : Store {
    private val data = mutableMapOf<String, String>()

    override fun get(key: String): String? = data[key]

    override fun put(key: String, value: String) {
        data[key] = value
    }
}

/** A decorator holding the interface it decorates. */
class AuditStore(private val inner: Store) : Store {
    private val log = mutableListOf<String>()

    override fun get(key: String): String? {
        note("get")
        return inner.get(key)
    }

    override fun put(key: String, value: String) {
        note("put")
        inner.put(key, value)
    }

    private fun note(entry: String) {
        log.add(entry)
    }
}

/** A top-level function, called by name — the case that must not be missed. */
fun normalize(value: String): String = value.trim()
