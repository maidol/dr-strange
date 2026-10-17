package acme;

import java.util.ArrayList;
import java.util.List;

/** A decorator holding the interface it decorates. */
public class AuditStore implements Store {
    private final Store inner;
    private final List<String> log = new ArrayList<>();

    public AuditStore(Store inner) {
        this.inner = inner;
    }

    @Override
    public String get(String key) {
        note("get");
        return inner.get(key);
    }

    @Override
    public void put(String key, String value) {
        note("put");
        inner.put(key, value);
    }

    private void note(String entry) {
        log.add(entry);
    }
}
