package acme;

/** Constructor injection: the field's type is the interface. */
public class Service {
    private final Store store;

    public Service(Store store) {
        this.store = store;
    }

    /** Through the interface — which implementation runs is a runtime fact. */
    public String load(String key) {
        return store.get(key);
    }

    /** Through the interface, plus a static whose target is written down. */
    public void save(String key, String value) {
        store.put(key, Text.normalize(value));
    }

    /** The parameter is typed Cache, so the target is knowable. */
    public String warm(Cache cache, int key) {
        return cache.get(key);
    }

    /** A call on `this` within the same class. */
    public String describe() {
        return label();
    }

    private String label() {
        return "service";
    }
}
