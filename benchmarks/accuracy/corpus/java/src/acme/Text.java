package acme;

/** A static helper, called by name — the case that must not be missed. */
public final class Text {
    private Text() {}

    public static String normalize(String value) {
        return value.trim();
    }
}
