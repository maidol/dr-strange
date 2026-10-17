package acme

/** The ways a Kotlin call target stops being written at the call site. */
object Dispatch {

    /** A lambda: its body compiles to a separate method. */
    fun viaLambda(value: String): String {
        val f: (String) -> String = { v -> normalize(v) }
        return f(value)
    }

    /** A function reference — the name appears as a value. */
    fun viaFunctionRef(value: String): String {
        val f: (String) -> String = ::normalize
        return f(value)
    }

    /** An extension function, resolved statically by receiver type. */
    fun String.shout(): String = normalize(this)

    fun viaExtension(value: String): String = value.shout()

    /** The plain case, for contrast. */
    fun direct(value: String): String = normalize(value)
}
