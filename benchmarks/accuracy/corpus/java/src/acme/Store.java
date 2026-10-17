package acme;

/** The interface a service depends on — the indirection a call goes through. */
public interface Store {
    String get(String key);

    void put(String key, String value);
}
