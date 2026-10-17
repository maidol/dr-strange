package acme;

import java.util.HashMap;
import java.util.Map;

/** One implementation. */
public class MemStore implements Store {
    private final Map<String, String> data = new HashMap<>();

    @Override
    public String get(String key) {
        return data.get(key);
    }

    @Override
    public void put(String key, String value) {
        data.put(key, value);
    }
}
