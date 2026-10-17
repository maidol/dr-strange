package acme;

import java.util.function.Function;

/** The ways a Java call target stops being written at the call site. */
public final class Dispatch {
    private Dispatch() {}

    /** A lambda: the body compiles to a synthetic method, reached by invokedynamic. */
    public static String viaLambda(String value) {
        Function<String, String> f = v -> Text.normalize(v);
        return f.apply(value);
    }

    /** A method reference — the name appears as a value, not at a call. */
    public static String viaMethodRef(String value) {
        Function<String, String> f = Text::normalize;
        return f.apply(value);
    }

    /** Reflection: the method name is a string. */
    public static Object viaReflection(Object target, String name) throws Exception {
        return target.getClass().getMethod(name).invoke(target);
    }

    /** The plain case, for contrast. */
    public static String direct(String value) {
        return Text.normalize(value);
    }
}
