package main

// A function value, a method value, and a closure: three ways a call target
// stops being written at the call site.

type handlerFunc func(string) string

func upper(s string) string { return s + "!" }

// viaFuncValue calls through a variable holding a function.
func viaFuncValue(s string) string {
	var h handlerFunc = upper
	return h(s)
}

// viaMethodValue calls through a bound method value.
func viaMethodValue(c *Cache, key int) string {
	get := c.Get
	return get(key)
}

// viaClosure calls a closure that calls a named function.
func viaClosure(s string) string {
	wrap := func(x string) string { return upper(x) }
	return wrap(s)
}

// deferred and goroutine call sites, which are calls all the same.
func viaDefer(c *Cache) {
	defer c.Get(1)
}

func viaGo(c *Cache) {
	go c.Get(2)
}
