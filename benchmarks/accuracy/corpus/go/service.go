package main

import "strings"

// Service holds the interface, never a concrete store.
type Service struct{ store Store }

// Load calls through the interface: the target depends on what was injected.
func (s *Service) Load(id string) (string, error) { return s.store.Get(id) }

// Save calls through the interface and then a package function.
func (s *Service) Save(id, v string) error {
	if err := s.store.Put(id, normalize(v)); err != nil {
		return err
	}
	return nil
}

// normalize is an ordinary package-level function — the easy case that must
// not be missed.
func normalize(v string) string { return strings.TrimSpace(v) }

// Reader is a second interface with a Get of its own, so the *name* `Get`
// alone does not identify a target.
type Reader interface{ Get(key int) string }

// Cache has a Get that is unrelated to Store.Get despite the shared name.
type Cache struct{ hits map[int]string }

func (c *Cache) Get(key int) string { return c.hits[key] }

// Warm calls Cache.Get, never Store.Get — a parser that matches on the bare
// method name will get this wrong.
func (s *Service) Warm(c *Cache, key int) string { return c.Get(key) }
