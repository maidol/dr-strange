package acme;

import java.util.HashMap;
import java.util.Map;

/** An unrelated class whose `get` shares a name with Store.get. */
public class Cache {
    private final Map<Integer, String> hits = new HashMap<>();

    public String get(int key) {
        return hits.get(key);
    }
}
